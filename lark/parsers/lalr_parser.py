"""This module implements a LALR(1) Parser
"""
# Author: Erez Shinan (2017)
# Email : erezshin@gmail.com
from ..exceptions import UnexpectedToken
from ..lexer import Token

from .lalr_analysis import LALR_Analyzer, Shift

class Parser:
    def __init__(self, parser_conf, debug=False):
        assert all(r.options is None or r.options.priority is None
                   for r in parser_conf.rules), "LALR doesn't yet support prioritization"
        analysis = LALR_Analyzer(parser_conf, debug=debug)
        analysis.compute_lookahead()
        callbacks = {rule: getattr(parser_conf.callback, rule.alias or rule.origin, None)
                          for rule in parser_conf.rules}

        self._parse_table = analysis.parse_table
        self.parser_conf = parser_conf
        self.parser = _Parser(analysis.parse_table, callbacks)
        self.parse = self.parser.parse

###{standalone

class _Parser:
    def __init__(self, parse_table, callbacks):
        self.states = parse_table.states
        self.start_state = parse_table.start_state
        self.end_state = parse_table.end_state
        self.callbacks = callbacks

    def get_action(self, parsing_state, token):
        state_stack = parsing_state[0]
        state = state_stack[-1]
        try:
            return self.states[state][token.type]
        except KeyError:
            expected = [s for s in self.states[state].keys() if s.isupper()]
            raise UnexpectedToken(token, expected, state=state)

    def reduce(self, parsing_state, rule):
        state_stack, value_stack = parsing_state
        size = len(rule.expansion)
        if size:
            s = value_stack[-size:]
            del state_stack[-size:]
            del value_stack[-size:]
        else:
            s = []

        value = self.callbacks[rule](s)

        _action, new_state = self.states[state_stack[-1]][rule.origin.name]
        assert _action is Shift
        state_stack.append(new_state)
        value_stack.append(value)

    def initial_parsing_state(self, set_state=None):
        if set_state: set_state(self.start_state)
        return (
            [self.start_state],  # state stack
            [],  # value stack
        )

    def next_parsing_state(self, parsing_state, token, set_state=None):
        """Produce parser state after consuming given token."""
        state_stack, value_stack = parsing_state
        while True:
            action, arg = self.get_action(parsing_state, token)

            if action is Shift:
                state_stack.append(arg)
                value_stack.append(token)
                if set_state: set_state(arg)
                break # next token
            else:
                self.reduce(parsing_state, arg)

    def parse(self, seq, set_state=None):
        token = None
        stream = iter(seq)

        parsing_state = self.initial_parsing_state(set_state)
        state_stack, value_stack = parsing_state

        # feed input tokens into the parser
        for token in stream:
            self.next_parsing_state(parsing_state, token, set_state)

        # feed end-of-input token into parser
        token = Token.new_borrow_pos('$END', '', token) if token else Token('$END', '', 0, 1, 1)
        self.next_parsing_state(parsing_state, token)

        # final sanity checks
        assert len(value_stack) == 2
        assert value_stack[1] == token

        return value_stack[0]

###}
