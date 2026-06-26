class OrExpr:
    def __init__(self, left, right):
        self.left = left
        self.right = right

    def match(self, tags):
        return self.left.match(tags) or self.right.match(tags)


class AndExpr:
    def __init__(self, left, right):
        self.left = left
        self.right = right

    def match(self, tags):
        return self.left.match(tags) and self.right.match(tags)


class NotExpr:
    def __init__(self, operand):
        self.operand = operand

    def match(self, tags):
        return not self.operand.match(tags)


class TagRef:
    def __init__(self, name):
        self.name = name

    def match(self, tags):
        return self.name in tags


class ParseError(Exception):
    pass


def _tokenize(expr):
    tokens = []
    i = 0
    while i < len(expr):
        if expr[i].isspace():
            i += 1
            continue

        if expr[i] in "()!&|":
            tokens.append(expr[i])
            i += 1
            continue

        j = i
        while j < len(expr) and (expr[j].isalnum() or expr[j] == '_'):
            j += 1

        if j == i:
            raise ParseError(f"unexpected character '{expr[i]}' at position {i}")
        if expr[i].isdigit():
            raise ParseError(f"identifier must not start with a digit: '{expr[i:j]}'")

        if expr[i:j] == "and":
            tokens.append('&')
        elif expr[i:j] == "or":
            tokens.append('|')
        elif expr[i:j] == "not":
            tokens.append('!')
        else:
            tokens.append(expr[i:j])

        i = j
    return tokens


class _Parser:
    """Recursive-descent parser for boolean tag expressions.

    expr     := or_expr
    or_expr  := and_expr ('|' and_expr)*
    and_expr := not_expr ('&' not_expr)*
    not_expr := '!' not_expr | operand
    operand  := '(' expr ')' | IDENTIFIER
    """

    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def next(self):
        token = self.peek()
        self.pos += 1
        return token

    def parse_or(self):
        left = self.parse_and()
        while self.peek() == '|':
            self.next()
            right = self.parse_and()
            left = OrExpr(left, right)
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.peek() == '&':
            self.next()
            right = self.parse_not()
            left = AndExpr(left, right)
        return left

    def parse_not(self):
        if self.peek() == '!':
            self.next()
            operand = self.parse_not()
            return NotExpr(operand)
        return self.parse_operand()

    def parse_operand(self):
        token = self.next()
        if token is None:
            raise ParseError("unexpected end of expression, expected an identifier or '('")
        if token in ('&', '|'):
            raise ParseError(f"unexpected operator '{token}', expected an identifier or '('")
        if token == ')':
            raise ParseError(f"unexpected ')', expected an identifier or '('")
        if token == '(':
            inner = self.parse_or()
            if self.next() != ')':
                raise ParseError("missing closing ')'")
            return inner
        return TagRef(token)

    def parse(self):
        result = self.parse_or()
        if self.peek() is not None:
            raise ParseError(f"unexpected trailing '{self.peek()}'")
        return result


def parse_tag_expression(expr_str):
    """Parse a boolean tag expression and return a matcher object.

    The matcher takes a set of tags and returns True if the tags satisfy
    the expression.

    Supported operators (lowest to highest precedence):
        |, or       logical OR
        &, and      logical AND
        !, not      logical NOT

    The keyword forms (and, or, not) are reserved and cannot be used as
    identifiers. Identifiers are tags to match against, consisting of
    alphanumeric characters and underscores, and must not start with a digit.

    Parentheses may be used for grouping.

    Examples:
        "smoke"                       -> has tag 'smoke'
        "!benchmark"                  -> does not have tag 'benchmark'
        "smoke & !experimental"       -> has 'smoke', not 'experimental'
        "validation | functional"     -> has either
        "!(benchmark | experimental)" -> has neither
    """
    tokens = _tokenize(expr_str)
    if not tokens:
        raise ParseError("empty tag expression")
    return _Parser(tokens).parse()
