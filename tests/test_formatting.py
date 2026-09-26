import unittest
from html import unescape
import re

from formatting import formatted_chunks


class FormattingTests(unittest.TestCase):
    def test_common_model_formatting(self):
        self.assertEqual(list(formatted_chunks('Value: **42** and *yes* or ~~no~~.')),
                         [('Value: <b>42</b> and <i>yes</i> or <s>no</s>.', 'Value: 42 and yes or no.')])

    def test_code_is_literal_and_html_is_escaped(self):
        self.assertEqual(list(formatted_chunks('`**x** < y`\n```python\na & b\n```')),
                         [('<code>**x** &lt; y</code>\n<pre>a &amp; b\n</pre>', '**x** < y\na & b\n')])
        self.assertEqual(list(formatted_chunks('<b>data</b> & **safe**'))[0][0],
                         '&lt;b&gt;data&lt;/b&gt; &amp; <b>safe</b>')

    def test_long_bold_and_code_are_balanced_in_every_chunk(self):
        for source in ['**' + '😀<&' * 3000 + '**', '```text\n' + '😀<&' * 3000 + '```']:
            parts = list(formatted_chunks(source))
            self.assertEqual(''.join(plain for _, plain in parts), '😀<&' * 3000)
            for html, plain in parts:
                self.assertLessEqual(len(plain.encode('utf-16-le')) // 2, 4000)
                self.assertEqual(unescape(re.sub(r'</?(?:b|pre)>', '', html)), plain)
                self.assertEqual(html.count('<b>'), html.count('</b>'))
                self.assertEqual(html.count('<pre>'), html.count('</pre>'))

    def test_incomplete_markup_and_identifiers_remain_readable(self):
        source = 'some_value **unfinished and 2 * 3'
        self.assertEqual(list(formatted_chunks(source)), [(source, source)])
