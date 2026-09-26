"""Workspace tools for Ollama's coding loop."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile

LIMIT = 32000


def schema(name, description, **properties):
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': {
                key: {'type': 'string', 'description': value} for key, value in properties.items()
            }, 'required': list(properties), 'additionalProperties': False}}}


class Workspace:
    def __init__(self, root, commands=False):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError('CODING_WORKSPACE must be an existing directory.')
        self.commands = commands
        self.tools = [
            schema('list_files', 'List a directory (up to 200 entries).', path='Relative directory, or .'),
            schema('read_file', 'Read a UTF-8 text file up to 32 KB.', path='Relative file path'),
            schema('write_file', 'Create or replace a UTF-8 file. Read existing files before changing them.',
                   path='Relative file path', content='Complete new contents'),
            schema('replace_text', 'Replace exactly one occurrence of text in an existing file.',
                   path='Relative file path', old='Exact nonempty text to replace', new='Replacement text'),
        ]
        if commands:
            self.tools.append(schema('run_command', 'Run a shell command in the project; 60 second timeout.',
                                     command='Shell command, such as python3 -m unittest discover -s tests'))

    def path(self, name):
        if not isinstance(name, str) or Path(name).is_absolute():
            raise ValueError('Use a relative workspace path.')
        path = self.root / name
        # Reject symlinks, traversal and private bot settings, including aliases.
        if '..' in Path(name).parts:
            raise ValueError('Parent traversal is not allowed.')
        for part in Path(name).parts:
            if part == '.git' or (part.startswith('.env') and part != '.env.example'):
                raise ValueError('This path is protected.')
        for candidate in [path, *path.parents]:
            if candidate == self.root:
                break
            if candidate.is_symlink():
                raise ValueError('Symlinks are not supported.')
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError('Path is outside the workspace.')
        if resolved.is_file() and resolved.stat().st_nlink > 1:
            raise ValueError('Hard-linked files are not supported.')
        return resolved

    def read(self, path):
        with path.open('rb') as file:
            data = file.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError('File exceeds the 32 KB editing limit.')
        return data.decode('utf-8')

    def execute(self, name, arguments):
        try:
            if name not in {item['function']['name'] for item in self.tools}:
                raise ValueError('Unknown or disabled tool.')
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            definition = next(t['function']['parameters'] for t in self.tools if t['function']['name'] == name)
            if not isinstance(arguments, dict) or set(arguments) != set(definition['required']):
                raise ValueError('Invalid tool arguments.')
            if not all(isinstance(v, str) for v in arguments.values()):
                raise ValueError('Tool arguments must be strings.')
            if name == 'run_command':
                return self.run(arguments['command'])
            path = self.path(arguments['path'])
            if name == 'list_files':
                entries = []
                for child in sorted(path.iterdir()):
                    try:
                        self.path(str(child.relative_to(self.root)))
                    except ValueError:
                        continue
                    entries.append(child.name + ('/' if child.is_dir() else ''))
                    if len(entries) == 200:
                        entries.append('[listing limited to 200 entries]')
                        break
                return '\n'.join(entries)[:LIMIT]
            if name == 'read_file':
                return self.read(path)
            content = arguments.get('content', '')
            if name == 'replace_text':
                content = self.read(path)
                old = arguments['old']
                if not old or content.count(old) != 1:
                    raise ValueError('old must match exactly once; read the file and retry.')
                content = content.replace(old, arguments['new'], 1)
            if len(content.encode('utf-8')) > LIMIT:
                raise ValueError('New contents exceed the 32 KB editing limit.')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
            return f'Updated {arguments["path"]}'
        except (OSError, ValueError, TypeError):
            # Do not expose OS errors containing absolute paths or private data.
            return 'Tool failed: invalid arguments, protected path, inaccessible file, non-UTF-8 or oversized file, or ambiguous replacement.'

    def run(self, command):
        # Shell commands are explicitly opt-in: cwd is NOT an OS sandbox.
        env = {key: value for key, value in os.environ.items()
               if key in {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SYSTEMROOT'}}
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(command, shell=True, cwd=self.root, env=env,
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            timed_out = False
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                # Also stop any descendants left behind by the shell.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            output.seek(0)
            data = output.read(LIMIT + 1)
        status = 'Timed out after 60 seconds' if timed_out else f'Exit code: {process.returncode}'
        return status + '\n' + data[:LIMIT].decode('utf-8', errors='replace') + (
            '\n[output truncated]' if len(data) > LIMIT else '')
