import re
import codecs

from .lexer import Lexer, Token, UnexpectedInput, TokenDef__Str, TokenDef__Regexp

from .parse_tree_builder import ParseTreeBuilder
from .parser_frontends import LALR
from .parsers.lalr_parser import UnexpectedToken
from .common import is_terminal, GrammarError, LexerConf, ParserConf

from .tree import Tree as T, Transformer, InlineTransformer, Visitor

unicode_escape = codecs.getdecoder('unicode_escape')

_TOKEN_NAMES = {
    '.' : 'DOT',
    ',' : 'COMMA',
    ':' : 'COLON',
    ';' : 'SEMICOLON',
    '+' : 'PLUS',
    '-' : 'MINUS',
    '*' : 'STAR',
    '/' : 'SLASH',
    '\\' : 'BACKSLASH',
    '|' : 'VBAR',
    '?' : 'QMARK',
    '!' : 'BANG',
    '@' : 'AT',
    '#' : 'HASH',
    '$' : 'DOLLAR',
    '%' : 'PERCENT',
    '^' : 'CIRCUMFLEX',
    '&' : 'AMPERSAND',
    '_' : 'UNDERSCORE',
    '<' : 'LESSTHAN',
    '>' : 'MORETHAN',
    '=' : 'EQUAL',
    '"' : 'DBLQUOTE',
    '\'' : 'QUOTE',
    '`' : 'BACKQUOTE',
    '~' : 'TILDE',
    '(' : 'LPAR',
    ')' : 'RPAR',
    '{' : 'LBRACE',
    '}' : 'RBRACE',
    '[' : 'LSQB',
    ']' : 'RSQB',
    '\n' : 'NEWLINE',
    '\r\n' : 'CRLF',
    '\t' : 'TAB',
    ' ' : 'SPACE',
}

# Grammar Parser
TOKENS = {
    '_LPAR': r'\(',
    '_RPAR': r'\)',
    '_LBRA': r'\[',
    '_RBRA': r'\]',
    'OP': '[+*?](?![a-z])',
    '_COLON': ':',
    '_OR': r'\|',
    '_DOT': r'\.',
    'RULE': '!?[_?]?[a-z][_a-z0-9]*',
    'TOKEN': '_?[A-Z][_A-Z0-9]*',
    'STRING': r'".*?[^\\]"',
    'REGEXP': r"/(?!/).*?[^\\]/",
    '_NL': r'(\r?\n)+\s*',
    'WS': r'[ \t]+',
    'COMMENT': r'//[^\n]*\n',
    '_TO': '->'
}

RULES = {
    'start': ['list'],
    'list':  ['item', 'list item'],
    'item':  ['rule', 'token', '_NL'],

    'rule': ['RULE _COLON expansions _NL'],
    'expansions': ['alias',
                   'expansions _OR alias',
                   'expansions _NL _OR alias'],

    '?alias':     ['expansion _TO RULE', 'expansion'],
    'expansion': ['_expansion'],

    '_expansion': ['', '_expansion expr'],

    '?expr': ['atom',
              'atom OP'],

    '?atom': ['_LPAR expansions _RPAR',
             'maybe',
             'RULE',
             'TOKEN',
             'anontoken'],

    'anontoken': ['tokenvalue'],

    'maybe': ['_LBRA expansions _RBRA'],

    'token': ['TOKEN _COLON tokenvalue _NL',
              'TOKEN tokenmods _COLON tokenvalue _NL'],

    '?tokenvalue': ['REGEXP', 'STRING'],
    'tokenmods':  ['_DOT RULE', 'tokenmods _DOT RULE'],
}


class EBNF_to_BNF(InlineTransformer):
    def __init__(self):
        self.new_rules = {}
        self.rules_by_expr = {}
        self.prefix = 'anon'
        self.i = 0

    def _add_recurse_rule(self, type_, expr):
        if expr in self.rules_by_expr:
            return self.rules_by_expr[expr]

        new_name = '__%s_%s_%d' % (self.prefix, type_, self.i)
        self.i += 1
        t = Token('RULE', new_name, -1)
        self.new_rules[new_name] = T('expansions', [T('expansion', [expr]), T('expansion', [t, expr])])
        self.rules_by_expr[expr] = t
        return t

    def expr(self, rule, op):
        if op.value == '?':
            return T('expansions', [rule, T('expansion', [])])
        elif op.value == '+':
            # a : b c+ d
            #   -->
            # a : b _c d
            # _c : _c c | c;
            return self._add_recurse_rule('plus', rule)
        elif op.value == '*':
            # a : b c* d
            #   -->
            # a : b _c? d
            # _c : _c c | c;
            new_name = self._add_recurse_rule('star', rule)
            return T('expansions', [new_name, T('expansion', [])])
        assert False, op


class SimplifyRule_Visitor(Visitor):

    @staticmethod
    def _flatten(tree):
        while True:
            to_expand = [i for i, child in enumerate(tree.children)
                         if isinstance(child, T) and child.data == tree.data]
            if not to_expand:
                break
            tree.expand_kids_by_index(*to_expand)


    def expansion(self, tree):
        # rules_list unpacking
        # a : b (c|d) e
        #  -->
        # a : b c e | b d e
        #
        # In AST terms:
        # expansion(b, expansions(c, d), e)
        #   -->
        # expansions( expansion(b, c, e), expansion(b, d, e) )

        while True:
            self._flatten(tree)

            for i, child in enumerate(tree.children):
                if isinstance(child, T) and child.data == 'expansions':
                    tree.data = 'expansions'
                    tree.children = [self.visit(T('expansion', [option if i==j else other
                                                                for j, other in enumerate(tree.children)]))
                                     for option in child.children]
                    break
            else:
                break

    def alias(self, tree):
        rule, alias_name = tree.children
        if rule.data == 'expansions':
            aliases = []
            for child in tree.children[0].children:
                aliases.append(T('alias', [child, alias_name]))
            tree.data = 'expansions'
            tree.children = aliases

    expansions = _flatten

def dict_update_safe(d1, d2):
    for k, v in d2.items():
        assert k not in d1
        d1[k] = v


class RuleTreeToText(Transformer):
    def expansions(self, x):
        return x
    def expansion(self, symbols):
        return [sym.value for sym in symbols], None
    def alias(self, x):
        (expansion, _alias), alias = x
        assert _alias is None, (alias, expansion, '-', _alias)
        return expansion, alias.value


class SimplifyTree(InlineTransformer):
    def maybe(self, expr):
        return T('expr', [expr, Token('OP', '?', -1)])

    def tokenmods(self, *args):
        if len(args) == 1:
            return list(args)
        tokenmods, value = args
        return tokenmods + [value]

def get_tokens(tree, token_set):
    for t in tree.find_data('token'):
        x = t.children
        name = x[0].value
        assert not name.startswith('__'), 'Names starting with double-underscore are reserved (Error at %s)' % name
        if name in token_set:
            raise ValueError("Token '%s' defined more than once" % name)
        token_set.add(name)

        if len(x) == 2:
            yield name, x[1], []
        else:
            assert len(x) == 3
            yield name, x[2], x[1]

class ExtractAnonTokens(InlineTransformer):
    def __init__(self, tokens, token_set):
        self.tokens = tokens
        self.token_set = token_set
        self.token_reverse = {value[1:-1]: name for name, value, _flags in tokens}
        self.i = 0

    def anontoken(self, token):
        if token.type == 'STRING':
            value = token.value[1:-1]
            try:
                # If already defined, use the user-defined token name
                token_name = self.token_reverse[value]
            except KeyError:
                # Try to assign an indicative anon-token name, otherwise use a numbered name
                try:
                    token_name = _TOKEN_NAMES[value]
                except KeyError:
                    if value.isalnum() and value[0].isalpha() and ('__'+value.upper()) not in self.token_set:
                        token_name = value.upper()  # This can create name duplications for unidentical tokens
                    else:
                        token_name = 'ANONSTR_%d' % self.i
                        self.i += 1
                token_name = '__' + token_name

        elif token.type == 'REGEXP':
            token_name = 'ANONRE_%d' % self.i
            value = token.value
            self.i += 1
        else:
            assert False, token

        if value in self.token_reverse: # Kind of a wierd placement
            token_name = self.token_reverse[value]

        if token_name not in self.token_set:
            self.token_set.add(token_name)
            self.tokens.append((token_name, token, []))
            assert value not in self.token_reverse, value
            self.token_reverse[value] = token_name

        return Token('TOKEN', token_name, -1)



class GrammarLoader:
    def __init__(self):
        tokens = [TokenDef__Regexp(name, value) for name, value in TOKENS.items()]

        d = {r: [(x.split(), None) for x in xs] for r, xs in RULES.items()}
        rules, callback = ParseTreeBuilder(T).create_tree_builder(d, None)
        lexer_conf = LexerConf(tokens, ['WS', 'COMMENT'], None)
        parser_conf = ParserConf(rules, callback, 'start')
        self.parser = LALR(lexer_conf, parser_conf)

        self.simplify_tree = SimplifyTree()
        self.simplify_rule = SimplifyRule_Visitor()
        self.rule_tree_to_text = RuleTreeToText()

    def load_grammar(self, grammar_text):
        try:
            tree = self.simplify_tree.transform( self.parser.parse(grammar_text+'\n') )
        except UnexpectedInput as e:
            raise GrammarError("Unexpected input %r at line %d column %d" % (e.context, e.line, e.column))
        except UnexpectedToken as e:
            if '_COLON' in e.expected:
                raise GrammarError("Missing colon at line %s column %s" % (e.line, e.column))
            elif 'tokenvalue' in e.expected:
                raise GrammarError("Expecting a value at line %s column %s" % (e.line, e.column))
            elif e.expected == ['_OR']:
                raise GrammarError("Newline without starting a new option (Expecting '|') at line %s column %s" % (e.line, e.column))
            raise

        # =================
        #  Process Tokens
        # =================

        token_set = set()
        tokens = list(get_tokens(tree, token_set))
        extract_anon = ExtractAnonTokens(tokens, token_set)
        tree = extract_anon.transform(tree) # Adds to tokens

        token_ref = {}
        tokendefs = []
        for name, token, flags in tokens:
            value = token.value[1:-1]
            if r'\u' in value:
                # XXX for now, you can't mix unicode escaping and unicode characters at the same token
                value = unicode_escape(value)[0]

            if token.type == 'REGEXP':
                sp = re.split(r'(\$\{%s})' % TOKENS['TOKEN'], value)
                if sp:
                    value = ''.join(token_ref[x[2:-1]] if x.startswith('${') and x.endswith('}') else x
                                    for x in sp)

                token_ref[name] = value
                tokendef = TokenDef__Regexp(name, value)
            else:
                assert token.type == 'STRING'
                tokendef = TokenDef__Str(name, value)

            tokendefs.append((tokendef, flags))

        # =================
        #  Process Rules
        # =================

        ebnf_to_bnf = EBNF_to_BNF()

        rules = {}
        for rule in tree.find_data('rule'):
            name, ebnf_tree = rule.children
            name = name.value
            if name in rules:
                raise ValueError("Rule '%s' defined more than once" % name)

            rules[name] = ebnf_to_bnf.transform(ebnf_tree)

        dict_update_safe(rules, ebnf_to_bnf.new_rules)

        for r in rules.values():
            self.simplify_rule.visit(r)

        rules = {origin: self.rule_tree_to_text.transform(tree) for origin, tree in rules.items()}

        # ====================
        #  Verify correctness
        # ====================
        used_symbols = {symbol for expansions in rules.values()
                               for expansion, _alias in expansions
                               for symbol in expansion}
        rule_set = {r.lstrip('!').lstrip('?') for r in rules}
        for sym in used_symbols:
            if is_terminal(sym):
                if sym not in token_set:
                    raise GrammarError("Token '%s' used but not defined" % sym)
            else:
                if sym not in rule_set:
                    raise GrammarError("Rule '%s' used but not defined" % sym)

        return tokendefs, rules

load_grammar = GrammarLoader().load_grammar