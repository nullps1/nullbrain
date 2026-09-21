"""Targeted deterministic review rules for the Numbers JDK21 profile, not a Java parser."""
import hashlib
import re

REVIEW_DEMO = '''public class Numbers {
    public static int max(int[] values) {
        if (values == null || values.length == 0) {
            throw new IllegalArgumentException("Array must not be null or empty");
        }
        int max = Integer.MIN_VALUE;
        for (int value : values) {
            if (value < Integer.MIN_VALUE || value > Integer.MAX_VALUE) {
                throw new IllegalArgumentException("Out of bounds");
            }
            if (value > max) max = value;
        }
        return max;
    }
}
'''


def code_only(source):
    # Preserve offsets and line numbers while ignoring comments and literals.
    pattern = r'"""[\s\S]*?"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/'
    return re.sub(pattern, lambda m: re.sub(r'[^\n]', ' ', m.group()), source)


def review_source(source, patch):
    code = code_only(source)
    findings = []
    def add(rule, offset, message):
        findings.append({'rule': rule, 'line': code.count('\n', 0, offset) + 1, 'message': message})
    # Deliberately narrow: plain declared int variables, not arbitrary expressions.
    integers = set(re.findall(r'\bint\s+(\w+)\s*(?=[=;,:)])', code))
    wider = set(re.findall(r'\b(?:long|double|float)\s+(\w+)\b', code))
    for name in sorted(integers - wider):
        variable = r'\b' + re.escape(name) + r'\b'
        patterns = [variable + r'\s*<\s*Integer\s*\.\s*MIN_VALUE\b',
                    variable + r'\s*>\s*Integer\s*\.\s*MAX_VALUE\b',
                    r'\bInteger\s*\.\s*MIN_VALUE\s*>\s*' + variable,
                    r'\bInteger\s*\.\s*MAX_VALUE\s*<\s*' + variable]
        for pattern in patterns:
            for match in re.finditer(pattern, code):
                add('int-range-check', match.start(),
                    f'Remove the impossible range check on int variable {name}; int already lies within Integer.MIN_VALUE and Integer.MAX_VALUE.')
    for match in re.finditer(r'\bstatic\s+void\s+main\s*\(', code):
        add('demo-main', match.start(), 'Remove the demonstration main method; this profile requests only the Numbers utility.')
    for match in re.finditer(r'\bSystem\s*\.\s*(?:out|err)\s*\.\s*print(?:ln|f)?\s*\(', code):
        add('console-output', match.start(), 'Remove console output from the Numbers utility.')
    return {'status': 'changes_requested' if findings else 'passed',
            'engine': 'numbers-targeted-rules-v1', 'findings': findings,
            'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'diff_sha256': hashlib.sha256(patch.encode()).hexdigest(),
            'scope': 'Three targeted checks; not a complete Java correctness or security review.'}
