"""
Joe Stroutさんが作ったMiniscriptをPythonへ移植したやつ

このコードは、Joe Strout 氏によって開発された公式の MiniScript 実装
(C# / C++版) をベースに、Python に移植したものです。

オリジナルの著作権:
    Copyright (c) 2016-2024 Joe Strout and contributors.
    https://github.com/JoeStrout/miniscript

この移植版の著作権:
    Copyright (c) 2026 かまぼこ陛下

この移植版は、オリジナルと同じ MIT License の下で配布されます。
ライセンス全文:
    https://github.com/JoeStrout/miniscript/blob/main/LICENSE
"""


from __future__ import annotations

import asyncio
import concurrent.futures
import math as _math
import random as _random
import sys
import time as _time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple


# ============================================================================
# 位置情報
# ============================================================================

class SourceLoc:
    def __init__(self, context: str = "", lineNum: int = 0):
        self.context = context
        self.lineNum = lineNum

    def is_empty(self) -> bool:
        return self.lineNum == 0 and self.context == ""

    def to_error_string(self) -> str:
        if not self.context:
            return f"[line {self.lineNum}]"
        return f"[{self.context} line {self.lineNum}]"

    def to_stack_string(self) -> str:
        ctx = self.context if self.context else "(current program)"
        return f"{ctx} line {self.lineNum}"


# ============================================================================
# 例外クラス
# ============================================================================

class MiniscriptException(Exception):
    def __init__(self, message: str, location: SourceLoc | None = None):
        super().__init__(message)
        self.message = message
        self.location = location or SourceLoc()

    def type_name(self) -> str:
        return "Error"

    def description(self) -> str:
        desc = f"{self.type_name()}: {self.message}"
        if self.location and self.location.lineNum > 0:
            desc += " " + self.location.to_error_string()
        return desc


class RuntimeMsException(MiniscriptException):
    def type_name(self) -> str:
        return "Runtime Error"


class TypeMsException(RuntimeMsException):
    def type_name(self) -> str:
        return "Type Error"


class IndexMsException(RuntimeMsException):
    def type_name(self) -> str:
        return "Index Error"


class TimeoutMsException(RuntimeMsException):
    def type_name(self) -> str:
        return "Timeout Error"


class LimitMsException(RuntimeMsException):
    pass


class UncaughtSignalMsException(RuntimeMsException):
    pass


# ============================================================================
# 停止・待機関連
# ============================================================================

class StopKind(Enum):
    WAIT = auto()
    YIELD = auto()
    DONE = auto()


@dataclass
class Stop:
    kind: StopKind
    seconds: float = 0.0


class MiniStop(Exception):
    pass


class MiniWait(MiniStop):
    def __init__(self, seconds: float):
        self.seconds = max(0.0, float(seconds))


class MiniYield(MiniStop):
    pass


# ============================================================================
# トークン
# ============================================================================

class TokenType:
    KEYWORD = "KEYWORD"
    IDENTIFIER = "IDENTIFIER"
    NUMBER = "NUMBER"
    STRING = "STRING"
    OP = "OPERATOR"
    EOL = "EOL"
    EOF = "EOF"
    UNKNOWN = "UNKNOWN"


class Token(NamedTuple):
    type: str
    value: str
    line: int
    column: int


KEYWORDS = {
    "if", "then", "else", "while", "for", "in",
    "function", "return",
    "and", "or", "not", "true", "false", "null", "isa",
    "new",
    "break", "continue",
    "else if", "end if", "end while", "end for", "end function"
}

MATH_ASSIGN_OPS = {"+=", "-=", "*=", "/=", "%=", "^="}
ASSIGN_OPS = {"="} | MATH_ASSIGN_OPS


# ============================================================================
# レクサー
# ============================================================================

class MiniScriptLexer:
    def __init__(self, source_code: str):
        self.source = source_code
        self.length = len(source_code)
        self.cursor = 0
        self.line = 1
        self.column = 1
        self.current_line = 1

    def current_sourceloc(self) -> SourceLoc:
        ln = int(self.current_line) if self.current_line is not None else 1
        return SourceLoc("", ln)

    def _peek(self, offset: int = 0) -> str:
        pos = self.cursor + offset
        if pos >= self.length:
            return ""
        return self.source[pos]

    def _advance(self, count: int = 1):
        for _ in range(count):
            if self.cursor >= self.length:
                break
            ch = self.source[self.cursor]
            if ch == "\n":
                self.line += 1
                self.column = 1
            else:
                self.column += 1
            self.cursor += 1

    def tokenize(self) -> List[Token]:
        raw: List[Token] = []
        prev_token = None

        while self.cursor < self.length:
            c = self._peek()

            if c in " \t\r":
                self._advance()
                continue

            if c == "/" and self._peek(1) == "/":
                while self._peek() and self._peek() != "\n":
                    self._advance()
                continue

            if c in "\n;":
                if self._should_suppress_eol(prev_token):
                    self._advance()
                    continue
                raw.append(Token(TokenType.EOL, c, self.line, self.column))
                self._advance()
                prev_token = raw[-1] if raw else None
                continue

            if c.isdigit() or (c == "." and self._peek(1).isdigit()):
                tok = self._read_number()
                raw.append(tok)
                prev_token = tok
                continue

            if c == '"':
                tok = self._read_string()
                raw.append(tok)
                prev_token = tok
                continue

            if c.isalpha() or c == "_":
                tok = self._read_identifier()
                raw.append(tok)
                prev_token = tok
                continue

            two = c + self._peek(1)
            if two in {"==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%=", "^="}:
                tok = Token(TokenType.OP, two, self.line, self.column)
                raw.append(tok)
                self._advance(2)
                prev_token = tok
                continue

            if c in "+-*/%=<>()[]{}:.,^@":
                tok = Token(TokenType.OP, c, self.line, self.column)
                raw.append(tok)
                self._advance()
                prev_token = tok
                continue

            tok = Token(TokenType.UNKNOWN, c, self.line, self.column)
            raw.append(tok)
            self._advance()
            prev_token = tok

        raw.append(Token(TokenType.EOF, "", self.line, self.column))
        return self._combine_compound_keywords(raw)

    def _combine_compound_keywords(self, tokens: List[Token]) -> List[Token]:
        combined: List[Token] = []
        i = 0
        while i < len(tokens):
            if i + 1 < len(tokens) and tokens[i].value == "end":
                nxt = tokens[i + 1]
                kw = f"end {nxt.value}"
                if kw in KEYWORDS:
                    combined.append(Token(TokenType.KEYWORD, kw, tokens[i].line, tokens[i].column))
                    i += 2
                    continue
            if i + 1 < len(tokens) and tokens[i].value == "else" and tokens[i + 1].value == "if":
                combined.append(Token(TokenType.KEYWORD, "else if", tokens[i].line, tokens[i].column))
                i += 2
                continue
            combined.append(tokens[i])
            i += 1
        return combined

    def _read_number(self) -> Token:
        start_line, start_col = self.line, self.column
        start_pos = self.cursor
        dot_count = 0
        while self.cursor < self.length:
            c = self._peek()
            if c == ".":
                if dot_count > 0:
                    break
                dot_count += 1
            elif not c.isdigit():
                break
            self._advance()
        return Token(TokenType.NUMBER, self.source[start_pos:self.cursor], start_line, start_col)

    def _read_string(self) -> Token:
        start_line, start_col = self.line, self.column
        self._advance()
        start_pos = self.cursor
        while self.cursor < self.length:
            c = self._peek()
            if c == '"':
                if self._peek(1) == '"':
                    self._advance(2)
                    continue
                break
            self._advance()
        val = self.source[start_pos:self.cursor].replace('""', '"')
        self._advance()
        return Token(TokenType.STRING, val, start_line, start_col)

    def _read_identifier(self) -> Token:
        start_line, start_col = self.line, self.column
        start_pos = self.cursor
        while self.cursor < self.length:
            c = self._peek()
            if c.isalnum() or c == "_":
                self._advance()
            else:
                break
        val = self.source[start_pos:self.cursor]
        t_type = TokenType.KEYWORD if val in KEYWORDS else TokenType.IDENTIFIER
        return Token(t_type, val, start_line, start_col)

    def _should_suppress_eol(self, prev_token: Optional[Token]) -> bool:
        if prev_token is None:
            return False

        continuation_tokens = {
            ",", "+", "-", "*", "/", "%", "^", "=",
            "(", "[", "{",
            "and", "or", "not", "isa"
        }

        if prev_token.type == TokenType.OP and prev_token.value in continuation_tokens:
            return True
        if prev_token.type == TokenType.KEYWORD and prev_token.value in continuation_tokens:
            return True
        return False


# ============================================================================
# AST（抽象構文木）
# ============================================================================

class ASTNode:
    pass


class Expr(ASTNode):
    pass


class Stmt(ASTNode):
    pass


class StringLiteral(Expr):
    def __init__(self, value: str):
        self.value = value

    def __repr__(self):
        return f"StringLiteral({self.value!r})"


class NumberLiteral(Expr):
    def __init__(self, value: str):
        self.value = float(value)

    def __repr__(self):
        return f"NumberLiteral({self.value})"


class BoolLiteral(Expr):
    def __init__(self, value: bool):
        self.value = 1.0 if value else 0.0


class NullLiteral(Expr):
    def __init__(self):
        pass


class RuntimeLiteral(Expr):
    def __init__(self, value: Any):
        self.value = value

    def __repr__(self):
        return f"RuntimeLiteral({self.value!r})"


class Variable(Expr):
    def __init__(self, name: str):
        self.name = name


class Lookup(Expr):
    def __init__(self, parent: Expr, name: str):
        self.parent = parent
        self.name = name


class IndexExpr(Expr):
    def __init__(self, target: Expr, start: Optional[Expr], end: Optional[Expr], is_slice: bool):
        self.target = target
        self.start = start
        self.end = end
        self.is_slice = is_slice


class ListLiteral(Expr):
    def __init__(self, items: List[Expr]):
        self.items = items


class MapLiteral(Expr):
    def __init__(self, pairs: List[Tuple[Expr, Expr]]):
        self.pairs = pairs


class UnaryOp(Expr):
    def __init__(self, op: str, expr: Expr):
        self.op = op
        self.expr = expr


class BinaryOp(Expr):
    def __init__(self, left: Expr, op: str, right: Expr):
        self.left = left
        self.op = op
        self.right = right


class CallExpr(Expr):
    def __init__(self, callee: Expr, args: List[Expr]):
        self.callee = callee
        self.args = args


@dataclass
class ParamSpec:
    name: str
    default_expr: Optional[Expr] = None


class FunctionLiteral(Expr):
    def __init__(self, params: List[ParamSpec], body: List[Stmt]):
        self.params = params
        self.body = body


class ExprStmt(Stmt):
    def __init__(self, expr: Expr):
        self.expr = expr


class IfStmt(Stmt):
    def __init__(self, branches: List[Tuple[Expr, List[Stmt]]], else_block: Optional[List[Stmt]]):
        self.branches = branches
        self.else_block = else_block


class WhileStmt(Stmt):
    def __init__(self, condition: Expr, body: List[Stmt]):
        self.condition = condition
        self.body = body


class ForStmt(Stmt):
    def __init__(self, var_name: str, iterable: Expr, body: List[Stmt]):
        self.var_name = var_name
        self.iterable = iterable
        self.body = body


class BreakStmt(Stmt):
    def __init__(self):
        pass


class ContinueStmt(Stmt):
    def __init__(self):
        pass


class ReturnStmt(Stmt):
    def __init__(self, expr: Optional[Expr]):
        self.expr = expr


class AssignStmt(Stmt):
    def __init__(self, target: Expr, op: str, expr: Expr):
        self.target = target
        self.op = op
        self.expr = expr


# ============================================================================
# パーサー
# ============================================================================

class MiniScriptParser:
    PREC_OR = 1
    PREC_AND = 2
    PREC_ISA = 4
    PREC_COMPARE = 5
    PREC_ADD = 6
    PREC_MUL = 7
    PREC_POWER = 11

    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.cursor = 0

    def _mark_line(self, node, tok: Token):
        node.line = tok.line
        return node

    def _peek(self) -> Token:
        if self.cursor >= len(self.tokens):
            return Token(TokenType.EOF, "", -1, -1)
        return self.tokens[self.cursor]

    def _advance(self) -> Token:
        tok = self._peek()
        self.cursor += 1
        return tok

    def _match(self, t_type: str, value: Optional[str] = None) -> bool:
        tok = self._peek()
        if tok.type != t_type:
            return False
        if value is not None and tok.value != value:
            return False
        self._advance()
        return True

    def _expect(self, t_type: str, value: Optional[str] = None) -> Token:
        tok = self._peek()
        if tok.type != t_type or (value is not None and tok.value != value):
            raise SyntaxError(f"Expected {t_type}({value}) but got {tok.type}({tok.value}) at line {tok.line}")
        return self._advance()

    def parse(self) -> List[Stmt]:
        out: List[Stmt] = []
        while self._peek().type != TokenType.EOF:
            if self._peek().type == TokenType.EOL:
                self._advance()
                continue
            stmt = self._parse_statement()
            if stmt is not None:
                out.append(stmt)
        return out

    def _parse_statement(self) -> Optional[Stmt]:
        tok0 = self._peek()
        tok = tok0

        if tok.type == TokenType.KEYWORD and tok.value == "if":
            return self._mark_line(self._parse_if_any(), tok0)
        if tok.type == TokenType.KEYWORD and tok.value == "while":
            return self._mark_line(self._parse_while(), tok0)
        if tok.type == TokenType.KEYWORD and tok.value == "for":
            return self._mark_line(self._parse_for(), tok0)
        if tok.type == TokenType.KEYWORD and tok.value == "break":
            self._advance()
            self._match(TokenType.EOL)
            return self._mark_line(BreakStmt(), tok0)
        if tok.type == TokenType.KEYWORD and tok.value == "continue":
            self._advance()
            self._match(TokenType.EOL)
            return self._mark_line(ContinueStmt(), tok0)
        if tok.type == TokenType.KEYWORD and tok.value == "return":
            self._advance()
            if self._peek().type == TokenType.EOL:
                self._advance()
                return self._mark_line(ReturnStmt(None), tok0)
            expr = self._parse_expression(0)
            self._match(TokenType.EOL)
            return self._mark_line(ReturnStmt(expr), tok0)

        # assignment
        if tok.type == TokenType.IDENTIFIER:
            save = self.cursor
            try:
                lvalue = self._parse_lvalue()
                op_tok = self._peek()
                if op_tok.type == TokenType.OP and op_tok.value in ASSIGN_OPS:
                    op = self._advance().value
                    rhs = self._parse_expression(0)
                    self._match(TokenType.EOL)
                    return self._mark_line(AssignStmt(lvalue, op, rhs), tok0)
            except SyntaxError:
                pass
            self.cursor = save

        # expression statement
        save = self.cursor
        try:
            expr = self._parse_expression(0)
            nxt = self._peek()
            if nxt.type in (TokenType.EOL, TokenType.EOF):
                self._match(TokenType.EOL)
                return self._mark_line(ExprStmt(expr), tok0)
        except SyntaxError:
            pass
        self.cursor = save

        # statement-level call: foo 1,2
        if tok.type == TokenType.IDENTIFIER:
            name_tok = self._advance()
            callee = Variable(name_tok.value)
            if self._peek().type == TokenType.EOL:
                self._advance()
                return self._mark_line(ExprStmt(callee), tok0)
            args: List[Expr] = [self._parse_expression(0)]
            while self._match(TokenType.OP, ","):
                args.append(self._parse_expression(0))
            self._match(TokenType.EOL)
            return self._mark_line(ExprStmt(CallExpr(callee, args)), tok0)

        self._advance()
        return None

    def _parse_if_any(self) -> Stmt:
        self._expect(TokenType.KEYWORD, "if")
        cond = self._parse_expression(0)
        self._expect(TokenType.KEYWORD, "then")

        if self._peek().type == TokenType.EOL:
            self._advance()
            return self._parse_if_multiline_after_then(cond)

        then_stmt = self._parse_single_inline_stmt_until({"else"})
        else_block: Optional[List[Stmt]] = None
        if self._match(TokenType.KEYWORD, "else"):
            else_stmt = self._parse_single_inline_stmt_until(set())
            else_block = [else_stmt]
        self._match(TokenType.EOL)
        return IfStmt([(cond, [then_stmt])], else_block)

    def _parse_single_inline_stmt_until(self, stop_keywords: set) -> Stmt:
        tok = self._peek()
        if tok.type == TokenType.KEYWORD and tok.value in stop_keywords:
            raise SyntaxError("Missing statement in short-form if")
        stmt = self._parse_statement()
        if stmt is None:
            raise SyntaxError("Invalid statement in short-form if")
        return stmt

    def _parse_if_multiline_after_then(self, first_cond: Expr) -> Stmt:
        branches: List[Tuple[Expr, List[Stmt]]] = []
        then_block = self._parse_block_until({"else if", "else", "end if"})
        branches.append((first_cond, then_block))

        while self._peek().type == TokenType.KEYWORD and self._peek().value == "else if":
            self._advance()
            c = self._parse_expression(0)
            self._expect(TokenType.KEYWORD, "then")
            self._match(TokenType.EOL)
            blk = self._parse_block_until({"else if", "else", "end if"})
            branches.append((c, blk))

        else_block: Optional[List[Stmt]] = None
        if self._peek().type == TokenType.KEYWORD and self._peek().value == "else":
            self._advance()
            self._match(TokenType.EOL)
            else_block = self._parse_block_until({"end if"})

        self._expect(TokenType.KEYWORD, "end if")
        self._match(TokenType.EOL)
        return IfStmt(branches, else_block)

    def _parse_while(self) -> Stmt:
        self._expect(TokenType.KEYWORD, "while")
        cond = self._parse_expression(0)
        self._match(TokenType.EOL)
        body = self._parse_block_until({"end while"})
        self._expect(TokenType.KEYWORD, "end while")
        self._match(TokenType.EOL)
        return WhileStmt(cond, body)

    def _parse_for(self) -> Stmt:
        self._expect(TokenType.KEYWORD, "for")
        name_tok = self._expect(TokenType.IDENTIFIER)
        self._expect(TokenType.KEYWORD, "in")
        iterable = self._parse_expression(0)
        self._match(TokenType.EOL)
        body = self._parse_block_until({"end for"})
        self._expect(TokenType.KEYWORD, "end for")
        self._match(TokenType.EOL)
        return ForStmt(name_tok.value, iterable, body)

    def _parse_block_until(self, stop: set) -> List[Stmt]:
        block: List[Stmt] = []
        while self._peek().type != TokenType.EOF:
            tok = self._peek()
            if tok.type == TokenType.KEYWORD and tok.value in stop:
                break
            if tok.type == TokenType.EOL:
                self._advance()
                continue
            stmt = self._parse_statement()
            if stmt is not None:
                block.append(stmt)
        return block

    def _parse_lvalue(self) -> Expr:
        base_tok = self._expect(TokenType.IDENTIFIER)
        expr: Expr = Variable(base_tok.value)

        while True:
            if self._match(TokenType.OP, "."):
                name_tok = self._peek()
                if name_tok.type not in (TokenType.IDENTIFIER, TokenType.KEYWORD):
                    raise SyntaxError(f"Expected name after '.' at line {name_tok.line}")
                self._advance()
                expr = Lookup(expr, name_tok.value)
                continue

            if self._match(TokenType.OP, "["):
                if self._match(TokenType.OP, ":"):
                    raise SyntaxError("Slice assignment is not supported")
                if self._match(TokenType.OP, "]"):
                    raise SyntaxError("Empty index [] is not allowed")
                start = self._parse_expression(0)
                if self._match(TokenType.OP, ":"):
                    raise SyntaxError("Slice assignment is not supported")
                self._expect(TokenType.OP, "]")
                expr = IndexExpr(expr, start, None, False)
                continue

            break
        return expr

    def _get_infix_prec(self, tok: Token) -> int:
        if tok.type == TokenType.KEYWORD:
            if tok.value == "or":
                return self.PREC_OR
            if tok.value == "and":
                return self.PREC_AND
            if tok.value == "isa":
                return self.PREC_ISA
            return -1
        if tok.type == TokenType.OP:
            if tok.value in ("==", "!=", "<", ">", "<=", ">="):
                return self.PREC_COMPARE
            if tok.value in ("+", "-"):
                return self.PREC_ADD
            if tok.value in ("*", "/", "%"):
                return self.PREC_MUL
            if tok.value == "^":
                return self.PREC_POWER
        return -1

    def _is_right_assoc(self, op: str) -> bool:
        return op == "^"

    def _parse_expression(self, min_prec: int) -> Expr:
        left = self._parse_prefix()
        left = self._parse_postfix(left)

        while True:
            tok = self._peek()
            prec = self._get_infix_prec(tok)
            if prec < min_prec:
                break
            op = self._advance().value
            next_min = prec if self._is_right_assoc(op) else (prec + 1)
            right = self._parse_expression(next_min)
            left = BinaryOp(left, op, right)
            left = self._parse_postfix(left)

        return left

    def _parse_prefix(self) -> Expr:
        tok = self._peek()

        if tok.type == TokenType.KEYWORD and tok.value == "new":
            self._advance()
            return UnaryOp("new", self._parse_expression(9))

        if tok.type == TokenType.OP and tok.value == "@":
            self._advance()
            return UnaryOp("@", self._parse_expression(10))

        if tok.type == TokenType.KEYWORD and tok.value == "not":
            self._advance()
            return UnaryOp("not", self._parse_expression(3))

        if tok.type == TokenType.OP and tok.value == "-":
            self._advance()
            return UnaryOp("-", self._parse_expression(8))

        return self._parse_primary()

    def _parse_postfix(self, expr: Expr) -> Expr:
        while True:
            if self._match(TokenType.OP, "."):
                name_tok = self._peek()
                if name_tok.type not in (TokenType.IDENTIFIER, TokenType.KEYWORD):
                    raise SyntaxError(f"Expected name after '.' at line {name_tok.line}")
                self._advance()
                expr = Lookup(expr, name_tok.value)
                continue

            if self._match(TokenType.OP, "["):
                start: Optional[Expr] = None
                end: Optional[Expr] = None
                is_slice = False

                if self._match(TokenType.OP, ":"):
                    is_slice = True
                    if not self._match(TokenType.OP, "]"):
                        end = self._parse_expression(0)
                        self._expect(TokenType.OP, "]")
                else:
                    if self._match(TokenType.OP, "]"):
                        raise SyntaxError("Empty index [] is not allowed")
                    start = self._parse_expression(0)
                    if self._match(TokenType.OP, ":"):
                        is_slice = True
                        if not self._match(TokenType.OP, "]"):
                            end = self._parse_expression(0)
                            self._expect(TokenType.OP, "]")
                    else:
                        self._expect(TokenType.OP, "]")

                expr = IndexExpr(expr, start, end, is_slice)
                continue

            if self._match(TokenType.OP, "("):
                args: List[Expr] = []
                if not self._match(TokenType.OP, ")"):
                    args.append(self._parse_expression(0))
                    while self._match(TokenType.OP, ","):
                        args.append(self._parse_expression(0))
                    self._expect(TokenType.OP, ")")
                expr = CallExpr(expr, args)
                continue

            break
        return expr

    def _parse_primary(self) -> Expr:
        tok = self._peek()

        if tok.type == TokenType.OP and tok.value == "(":
            self._advance()
            e = self._parse_expression(0)
            self._expect(TokenType.OP, ")")
            return e

        if tok.type == TokenType.KEYWORD and tok.value == "function":
            return self._parse_function_literal()

        if tok.type == TokenType.STRING:
            self._advance()
            return self._mark_line(StringLiteral(tok.value), tok)

        if tok.type == TokenType.NUMBER:
            self._advance()
            return self._mark_line(NumberLiteral(tok.value), tok)

        if tok.type == TokenType.KEYWORD and tok.value in ("true", "false", "null"):
            self._advance()
            if tok.value == "true":
                return BoolLiteral(True)
            if tok.value == "false":
                return BoolLiteral(False)
            return NullLiteral()

        if tok.type == TokenType.OP and tok.value == "[":
            return self._parse_list_literal()

        if tok.type == TokenType.OP and tok.value == "{":
            return self._parse_map_literal()

        if tok.type == TokenType.IDENTIFIER:
            self._advance()
            return self._mark_line(Variable(tok.value), tok)

        raise SyntaxError(f"Unexpected token: {tok.value} at line {tok.line}")

    def _parse_list_literal(self) -> Expr:
        self._expect(TokenType.OP, "[")
        items: List[Expr] = []
        if self._match(TokenType.OP, "]"):
            return ListLiteral(items)
        while True:
            items.append(self._parse_expression(0))
            if self._match(TokenType.OP, "]"):
                break
            self._expect(TokenType.OP, ",")
        return ListLiteral(items)

    def _parse_map_literal(self) -> Expr:
        self._expect(TokenType.OP, "{")
        pairs: List[Tuple[Expr, Expr]] = []
        if self._match(TokenType.OP, "}"):
            return MapLiteral(pairs)
        while True:
            k = self._parse_expression(0)
            self._expect(TokenType.OP, ":")
            v = self._parse_expression(0)
            pairs.append((k, v))
            if self._match(TokenType.OP, "}"):
                break
            self._expect(TokenType.OP, ",")
        return MapLiteral(pairs)

    def _parse_function_literal(self) -> Expr:
        self._expect(TokenType.KEYWORD, "function")
        params: List[ParamSpec] = []

        if self._match(TokenType.OP, "("):
            if not self._match(TokenType.OP, ")"):
                while True:
                    name_tok = self._expect(TokenType.IDENTIFIER)
                    default_expr: Optional[Expr] = None
                    if self._match(TokenType.OP, "="):
                        default_expr = self._parse_expression(0)
                    params.append(ParamSpec(name_tok.value, default_expr))
                    if self._match(TokenType.OP, ")"):
                        break
                    self._expect(TokenType.OP, ",")

        self._match(TokenType.EOL)
        body = self._parse_block_until({"end function"})
        self._expect(TokenType.KEYWORD, "end function")
        self._match(TokenType.EOL)
        return FunctionLiteral(params, body)


# ============================================================================
# 実行時環境
# ============================================================================

class Environment:
    def __init__(self, values: Optional[Dict[str, Any]] = None, parent: Optional["Environment"] = None):
        self.values = values if values is not None else {}
        self.parent = parent

    def get(self, name: str) -> Any:
        if name in self.values:
            return self.values[name]
        if self.parent is not None:
            return self.parent.get(name)
        raise NameError(f"Undefined variable: '{name}'")

    def set_local(self, name: str, value: Any):
        self.values[name] = value

    def set_existing_or_local(self, name, value):
        env = self
        while env is not None:
            if name in env.values:
                env.values[name] = value
                return
            env = env.parent
        self.values[name] = value


@dataclass
class FunctionValue:
    params: List[ParamSpec]
    body: List[Stmt]
    closure: Environment


@dataclass
class NativeFunctionValue:
    func: Callable[[Any, List[Any]], Any]


class _BreakSignal(Exception):
    pass


class _ContinueSignal(Exception):
    pass


class _ReturnSignal(Exception):
    def __init__(self, value: Any):
        self.value = value


class OuterProxy:
    def __init__(self, outer_env: Environment):
        self.outer_env = outer_env

    def get(self, key: str) -> Any:
        return self.outer_env.values.get(key, None)

    def set(self, key: str, value: Any):
        self.outer_env.values[key] = value


@dataclass
class SuperProxy:
    start_map: Optional[dict]
    self_obj: dict


@dataclass
class BoundMethod:
    func: Any
    self_obj: Any
    super_start: Optional[dict]


@dataclass
class _StmtFrame:
    statements: List["Stmt"]
    index: int = 0
    restore_env: Optional["Environment"] = None
    for_items: Optional[List[Any]] = None
    for_var_name: Optional[str] = None
    for_index: int = 0
    for_body: Optional[List["Stmt"]] = None


# ============================================================================
# ヘルパー関数（文字列変換・型変換など）
# ============================================================================

def _num_to_string_miniscript(x: float) -> str:
    x = float(x)
    if _math.fmod(x, 1.0) == 0.0:
        return f"{x:.0f}"
    ax = abs(x)
    if ax > 1e10 or (ax < 1e-6 and ax > 0.0):
        return f"{x:.6E}"
    s = f"{x:.6f}"
    i = len(s) - 1
    while i > 1 and s[i] == "0" and s[i - 1] != ".":
        i -= 1
    if i + 1 < len(s):
        s = s[: i + 1]
    return s


def _ms_quote_string(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def miniscript_to_string(val: Any) -> str:
    return _ms_to_string(val, quote_strings=False, recursion_limit_for_collections=3)


def _ms_to_codeform(val: Any, recursion_limit: int) -> str:
    if val is None:
        return "null"
    if isinstance(val, (int, float)):
        return _num_to_string_miniscript(float(val))
    if isinstance(val, str):
        return _ms_quote_string(val)
    if isinstance(val, list):
        if recursion_limit <= 0:
            return "[...]"
        if len(val) == 0:
            return "[]"
        return "[" + ", ".join(_ms_to_codeform(x, recursion_limit - 1) for x in val) + "]"
    if isinstance(val, dict):
        if recursion_limit <= 0:
            return "{...}"
        items = [(k, v) for (k, v) in val.items() if k != "_isa"]
        if not items:
            return "{}"
        parts = []
        for k, v in items:
            parts.append(f"{_ms_to_codeform(k, recursion_limit - 1)}: {_ms_to_codeform(v, recursion_limit - 1)}")
        return "{" + ", ".join(parts) + "}"
    return str(val)


def _ms_to_string(val: Any, quote_strings: bool, recursion_limit_for_collections: int) -> str:
    if val is None:
        return "null"
    if isinstance(val, (int, float)):
        return _num_to_string_miniscript(float(val))
    if isinstance(val, str):
        return _ms_quote_string(val) if quote_strings else val
    if isinstance(val, list) or isinstance(val, dict):
        return _ms_to_codeform(val, recursion_limit_for_collections)
    return str(val)


def miniscript_truthy(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return len(val) > 0
    if isinstance(val, list):
        return len(val) > 0
    if isinstance(val, dict):
        return len([k for k in val.keys() if k != "_isa"]) > 0
    return True


def boolnum(val: Any) -> float:
    return 1.0 if miniscript_truthy(val) else 0.0


def clamp01(x: float) -> float:
    if x < 0:
        return 0.0
    if x > 1:
        return 1.0
    return x


def _as_number(x: Any) -> float:
    if isinstance(x, (int, float)):
        return float(x)
    return boolnum(x)


def _as_int_index(x: Any) -> int:
    n = float(x)
    if not n.is_integer():
        raise TypeError("Index must be an integer value")
    return int(n)


def _normalize_index(i: int, length: int) -> int:
    if i < 0:
        i = length + i
    return i


def _replicate_sequence(seq: Any, n: float):
    if n <= 0:
        return seq[:0]
    whole = int(n)
    frac = n - whole
    if isinstance(seq, str):
        base = seq * whole
        if frac > 0:
            base += seq[: int(len(seq) * frac)]
        return base
    if isinstance(seq, list):
        base = seq * whole
        if frac > 0:
            base += seq[: int(len(seq) * frac)]
        return base
    raise TypeError("Replication is supported on strings and lists only")


def _get_isa(x: Any) -> Optional[dict]:
    if isinstance(x, dict):
        v = x.get("_isa", None)
        return v if isinstance(v, dict) else None
    return None


def _map_get_with_defining_map(obj: dict, key: Any) -> Tuple[Any, Optional[dict]]:
    cur: Optional[dict] = obj
    while isinstance(cur, dict):
        if key in cur:
            return cur[key], cur
        cur = _get_isa(cur)
    return None, None


def _isa_chain_contains(obj: dict, target: dict) -> bool:
    cur: Optional[dict] = obj
    while isinstance(cur, dict):
        if cur is target:
            return True
        cur = _get_isa(cur)
    return False


def _range_impl(x: float, y: float = 0.0, step: Optional[float] = None) -> List[float]:
    if step is None:
        step = 1.0 if y >= x else -1.0
    if step == 0:
        return []
    out: List[float] = []
    v = x
    if step > 0:
        while v <= y:
            out.append(float(v))
            v += step
    else:
        while v >= y:
            out.append(float(v))
            v += step
    return out


# ============================================================================
# インタプリタ
# ============================================================================

class MiniScriptInterpreter:
    def __init__(self, global_env: Environment, map_class: dict):
        self.global_env = global_env
        self.env = global_env
        self.start_time = _time.time()
        self.map_class = map_class
        self.call_stack: List[SourceLoc] = [SourceLoc("", 1)]
        self.rng = _random.Random()
        self.current_line = 1
        self.output_parts: List[str] = []
        self._exec_frames: List[_StmtFrame] = []
        self.cooperative = False

    def _advance_after_stop(self):
        if self._exec_frames:
            self._exec_frames[-1].index += 1

    def _run_current_frame_until_stop(self):
        while self._exec_frames:
            frame = self._exec_frames[-1]

            # Cooperative for-loop frame
            if frame.for_items is not None:
                if frame.for_index >= len(frame.for_items):
                    self._exec_frames.pop()
                    continue
                self.env.set_local(frame.for_var_name, frame.for_items[frame.for_index])
                frame.for_index += 1
                body_frame = _StmtFrame(frame.for_body)
                self._exec_frames.append(body_frame)
                try:
                    self._run_current_frame_until_stop()
                except MiniStop:
                    raise
                except _BreakSignal:
                    if self._exec_frames and self._exec_frames[-1] is body_frame:
                        self._exec_frames.pop()
                    if self._exec_frames and self._exec_frames[-1] is frame:
                        self._exec_frames.pop()
                    return
                except _ContinueSignal:
                    if self._exec_frames and self._exec_frames[-1] is body_frame:
                        self._exec_frames.pop()
                    continue
                if self._exec_frames and self._exec_frames[-1] is body_frame:
                    self._exec_frames.pop()
                continue

            if frame.index >= len(frame.statements):
                restore = frame.restore_env
                self._exec_frames.pop()
                if restore is not None:
                    self.env = restore
                    if self._exec_frames:
                        self._exec_frames[-1].index += 1
                continue

            self.execute(frame.statements[frame.index])
            frame.index += 1

    def write_output(self, s: str):
        self.output_parts.append(s)

    def _inject_system_vars(self, call_env: Environment):
        call_env.set_local("globals", self.global_env.values)
        call_env.set_local("locals", call_env.values)
        try:
            call_env.set_local("intrinsics", self.global_env.get("intrinsics"))
        except Exception:
            pass

    def current_sourceloc(self) -> SourceLoc:
        ln = int(self.current_line) if self.current_line is not None else 1
        return SourceLoc("", ln)

    def _push_frame(self, name: str = ""):
        self.call_stack.append(SourceLoc("", int(self.current_line or 1)))

    def _pop_frame(self):
        if self.call_stack:
            self.call_stack.pop()

    def intrinsic_len(self, x: Any) -> float:
        if x is None:
            return 0.0
        if isinstance(x, str):
            return float(len(x))
        if isinstance(x, list):
            return float(len(x))
        if isinstance(x, dict):
            return float(len([k for k in x.keys() if k != "_isa"]))
        return 0.0

    def intrinsic_indexes(self, x: Any) -> list:
        if isinstance(x, str):
            return [] if len(x) == 0 else _range_impl(0, len(x) - 1, None)
        if isinstance(x, list):
            return [] if len(x) == 0 else _range_impl(0, len(x) - 1, None)
        if isinstance(x, dict):
            return [k for k in x.keys() if k != "_isa"]
        return []

    def intrinsic_values(self, x: Any) -> list:
        if isinstance(x, str):
            return list(x)
        if isinstance(x, list):
            return list(x)
        if isinstance(x, dict):
            return [v for k, v in x.items() if k != "_isa"]
        return []

    def execute_statements(self, statements: List[Stmt]):
        if self.cooperative:
            if not self._exec_frames or self._exec_frames[-1].statements is not statements:
                self._exec_frames.append(_StmtFrame(statements))
            self._run_current_frame_until_stop()
            return
        for s in statements:
            self.execute(s)

    def execute(self, stmt: Stmt):
        ln = None
        if hasattr(stmt, "line"):
            ln = getattr(stmt, "line")
        elif hasattr(stmt, "lineNum"):
            ln = getattr(stmt, "lineNum")
        elif hasattr(stmt, "token") and hasattr(stmt.token, "line"):
            ln = stmt.token.line

        if ln is not None:
            self.current_line = int(ln)

        if getattr(self, "call_stack", None):
            self.call_stack[-1].lineNum = int(self.current_line or 1)

        if isinstance(stmt, ExprStmt):
            self.evaluate(stmt.expr)
            return

        if isinstance(stmt, AssignStmt):
            rhs = self.evaluate(stmt.expr)
            if stmt.op == "=":
                self._set_lvalue(stmt.target, rhs)
                return
            cur = self._get_lvalue(stmt.target)
            opmap = {"+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%", "^=": "^"}
            new_val = self._apply_binary(cur, opmap[stmt.op], rhs)
            self._set_lvalue(stmt.target, new_val)
            return

        if isinstance(stmt, ReturnStmt):
            val = None if stmt.expr is None else self.evaluate(stmt.expr)
            raise _ReturnSignal(val)

        if isinstance(stmt, IfStmt):
            for cond, block in stmt.branches:
                if miniscript_truthy(self.evaluate(cond)):
                    self.execute_statements(block)
                    return
            if stmt.else_block is not None:
                self.execute_statements(stmt.else_block)
            return

        if isinstance(stmt, WhileStmt):
            if self.cooperative:
                while miniscript_truthy(self.evaluate(stmt.condition)):
                    try:
                        if not self._exec_frames or self._exec_frames[-1].statements is not stmt.body:
                            self._exec_frames.append(_StmtFrame(stmt.body))
                        self._run_current_frame_until_stop()
                    except MiniStop:
                        raise
                    except _ContinueSignal:
                        if self._exec_frames and self._exec_frames[-1].statements is stmt.body:
                            self._exec_frames.pop()
                        continue
                    except _BreakSignal:
                        if self._exec_frames and self._exec_frames[-1].statements is stmt.body:
                            self._exec_frames.pop()
                        break
                if self._exec_frames and self._exec_frames[-1].statements is stmt.body:
                    self._exec_frames.pop()
                return
            while miniscript_truthy(self.evaluate(stmt.condition)):
                try:
                    self.execute_statements(stmt.body)
                except _ContinueSignal:
                    continue
                except _BreakSignal:
                    break
            return

        if isinstance(stmt, ForStmt):
            it = self.evaluate(stmt.iterable)
            items: Optional[List[Any]] = None
            if isinstance(it, list):
                items = list(it)
            elif isinstance(it, str):
                items = list(it)
            elif isinstance(it, dict):
                items = [{"key": k, "value": v, "_isa": self.map_class} for k, v in it.items() if k != "_isa"]
            else:
                raise TypeMsException("for ... in expects a list, string, or map", self.current_sourceloc())

            if self.cooperative:
                fframe = _StmtFrame(statements=[])
                fframe.for_items = items
                fframe.for_var_name = stmt.var_name
                fframe.for_index = 0
                fframe.for_body = stmt.body
                self._exec_frames.append(fframe)
                return
            for item in items:
                self.env.set_local(stmt.var_name, item)
                try:
                    self.execute_statements(stmt.body)
                except _ContinueSignal:
                    continue
                except _BreakSignal:
                    break
            return

        if isinstance(stmt, BreakStmt):
            raise _BreakSignal()

        if isinstance(stmt, ContinueStmt):
            raise _ContinueSignal()

        raise RuntimeMsException(f"Unknown statement type: {type(stmt).__name__}", self.current_sourceloc())

    def evaluate(self, expr: Expr):
        ln = None
        if hasattr(expr, "line"):
            ln = getattr(expr, "line")
        if ln is not None:
            self.current_line = int(ln)

        if isinstance(expr, RuntimeLiteral):
            return expr.value
        if isinstance(expr, StringLiteral):
            return expr.value
        if isinstance(expr, NumberLiteral):
            return expr.value
        if isinstance(expr, BoolLiteral):
            return expr.value
        if isinstance(expr, NullLiteral):
            return None

        if isinstance(expr, FunctionLiteral):
            return FunctionValue(expr.params, expr.body, self.env)

        if isinstance(expr, UnaryOp) and expr.op == "@":
            if isinstance(expr.expr, Variable):
                return self.env.get(expr.expr.name)
            raise RuntimeError("@ is only supported on identifiers")

        if isinstance(expr, UnaryOp) and expr.op == "new":
            proto = self.evaluate(expr.expr)
            if not isinstance(proto, dict):
                raise TypeError("new expects a map/class object")
            return {"_isa": proto}

        if isinstance(expr, Variable):
            val = self.env.get(expr.name)
            if isinstance(val, (FunctionValue, NativeFunctionValue, BoundMethod)):
                return self._call_value(val, [], call_name=expr.name)
            return val

        if isinstance(expr, CallExpr):
            callee_val, call_name = self._eval_callee(expr.callee, want_name=True)
            args = [self.evaluate(a) for a in expr.args]
            return self._call_value(callee_val, args, call_name=call_name)

        if isinstance(expr, Lookup):
            callee_val, call_name = self._eval_callee(expr, want_name=True)
            if isinstance(callee_val, (FunctionValue, NativeFunctionValue, BoundMethod)):
                return self._call_value(callee_val, [], call_name=call_name)
            return callee_val

        if isinstance(expr, ListLiteral):
            return [self.evaluate(e) for e in expr.items]

        if isinstance(expr, MapLiteral):
            d = {"_isa": self.map_class}
            for k_expr, v_expr in expr.pairs:
                d[self.evaluate(k_expr)] = self.evaluate(v_expr)
            return d

        if isinstance(expr, IndexExpr):
            target = self.evaluate(expr.target)
            if expr.is_slice:
                return self._eval_slice(target, expr.start, expr.end)
            return self._index_get(target, self.evaluate(expr.start) if expr.start else None)

        if isinstance(expr, UnaryOp):
            v = self.evaluate(expr.expr)
            if expr.op == "not":
                a = _as_number(v)
                return clamp01(1.0 - abs(a))
            if expr.op == "-":
                return -float(v)
            raise RuntimeError(f"Unknown unary operator: {expr.op}")

        if isinstance(expr, BinaryOp):
            if expr.op == "isa":
                left = self.evaluate(expr.left)
                right = self._eval_no_autocall(expr.right)
                return self._apply_isa(left, right)

            left = self.evaluate(expr.left)
            right = self.evaluate(expr.right)
            return self._apply_binary(left, expr.op, right)

        raise RuntimeError(f"Unknown expression type: {type(expr).__name__}")

    def _eval_no_autocall(self, expr: Expr) -> Any:
        if isinstance(expr, Variable):
            return self.env.get(expr.name)
        if isinstance(expr, Lookup):
            v, _ = self._eval_callee(expr, want_name=False)
            return v
        return self.evaluate(expr)

    def _apply_isa(self, left: Any, right: Any) -> float:
        try:
            number_class = self.global_env.get("number")
            string_class = self.global_env.get("string")
            list_class = self.global_env.get("list")
            map_class = self.global_env.get("map")
        except Exception:
            number_class = string_class = list_class = map_class = None

        if right is number_class:
            return 1.0 if isinstance(left, (int, float)) else 0.0
        if right is string_class:
            return 1.0 if isinstance(left, str) else 0.0
        if right is list_class:
            return 1.0 if isinstance(left, list) else 0.0
        if right is map_class:
            return 1.0 if isinstance(left, dict) else 0.0

        if isinstance(left, dict) and isinstance(right, dict):
            return 1.0 if _isa_chain_contains(left, right) else 0.0
        return 0.0

    def _eval_callee(self, expr: Expr, want_name: bool) -> Tuple[Any, str]:
        if isinstance(expr, Variable):
            v = self.env.get(expr.name)
            return v, (expr.name if want_name else "")

        if isinstance(expr, Lookup):
            parent = self.evaluate(expr.parent)
            name = expr.name

            if name == "len":
                return self.intrinsic_len(parent), ("len" if want_name else "")
            if name == "indexes":
                return self.intrinsic_indexes(parent), ("indexes" if want_name else "")
            if name == "values":
                return self.intrinsic_values(parent), ("values" if want_name else "")

            if isinstance(parent, OuterProxy):
                return parent.get(name), (f"outer.{name}" if want_name else "")

            if isinstance(parent, SuperProxy):
                start = parent.start_map
                if not isinstance(start, dict):
                    return None, (f"super.{name}" if want_name else "")
                val, defining = _map_get_with_defining_map(start, name)
                if isinstance(val, (FunctionValue, NativeFunctionValue)):
                    return BoundMethod(val, parent.self_obj, _get_isa(defining) if defining else None), (f"super.{name}" if want_name else "")
                return val, (f"super.{name}" if want_name else "")

            if isinstance(parent, dict):
                val, defining = _map_get_with_defining_map(parent, name)
                if defining is not None:
                    if isinstance(val, (FunctionValue, NativeFunctionValue)):
                        return BoundMethod(val, parent, _get_isa(defining) if defining else None), (f"{name}" if want_name else "")
                    return val, (f"{name}" if want_name else "")

                map_class = self.global_env.get("map")
                if isinstance(map_class, dict):
                    v2, def2 = _map_get_with_defining_map(map_class, name)
                    if def2 is not None:
                        if isinstance(v2, (FunctionValue, NativeFunctionValue)):
                            return BoundMethod(v2, parent, _get_isa(def2) if def2 else None), (f"{name}" if want_name else "")
                        return v2, (f"{name}" if want_name else "")
                return None, (f"{name}" if want_name else "")

            if isinstance(parent, str):
                cls = self.global_env.get("string")
                return self._lookup_type_method(cls, parent, name, want_name)
            if isinstance(parent, list):
                cls = self.global_env.get("list")
                return self._lookup_type_method(cls, parent, name, want_name)
            if isinstance(parent, (int, float)):
                cls = self.global_env.get("number")
                return self._lookup_type_method(cls, float(parent), name, want_name)

            raise TypeError("Dot lookup is only supported on maps/strings/lists/numbers/outer/super")

        v = self.evaluate(expr)
        return v, ("<expr>" if want_name else "")

    def _lookup_type_method(self, cls_map: Any, self_obj: Any, name: str, want_name: bool) -> Tuple[Any, str]:
        if isinstance(cls_map, dict):
            v, defining = _map_get_with_defining_map(cls_map, name)
            if defining is not None:
                if isinstance(v, (FunctionValue, NativeFunctionValue)):
                    return BoundMethod(v, self_obj, _get_isa(defining) if defining else None), (name if want_name else "")
                return v, (name if want_name else "")
        return None, (name if want_name else "")

    def _call_value(self, callee_val: Any, args: List[Any], call_name: str) -> Any:
        if isinstance(callee_val, BoundMethod):
            return self._call_function_like(
                callee_val.func, args,
                bound_self=callee_val.self_obj,
                super_start=callee_val.super_start,
                call_name=call_name
            )
        return self._call_function_like(callee_val, args, bound_self=None, super_start=None, call_name=call_name)

    def _call_function_like(self, func_val: Any, args: List[Any],
                            bound_self: Optional[Any],
                            super_start: Optional[dict],
                            call_name: str) -> Any:
        if isinstance(func_val, NativeFunctionValue):
            saved_env = self.env
            call_env = None
            if bound_self is not None:
                call_env = Environment(parent=self.env)
                call_env.set_local("self", bound_self)
                if isinstance(bound_self, dict):
                    call_env.set_local("super", SuperProxy(super_start, bound_self))
                self._inject_system_vars(call_env)
                self.env = call_env

            try:
                return func_val.func(self, args)
            finally:
                self.env = saved_env

        if isinstance(func_val, FunctionValue):
            saved_env = self.env
            call_env = Environment(parent=func_val.closure)

            self._inject_system_vars(call_env)
            call_env.set_local("outer", OuterProxy(func_val.closure))

            if bound_self is not None:
                call_env.set_local("self", bound_self)
                if isinstance(bound_self, dict):
                    call_env.set_local("super", SuperProxy(super_start, bound_self))

            for i, p in enumerate(func_val.params):
                if i < len(args):
                    call_env.set_local(p.name, args[i])
                else:
                    if p.default_expr is not None:
                        self.env = call_env
                        dv = self.evaluate(p.default_expr)
                        call_env.set_local(p.name, dv)
                    else:
                        call_env.set_local(p.name, None)

            self.env = call_env
            self._push_frame(call_name or "<function>")
            suspended = False
            try:
                if self.cooperative:
                    if not self._exec_frames or self._exec_frames[-1].statements is not func_val.body:
                        self._exec_frames.append(_StmtFrame(func_val.body, restore_env=saved_env))
                    self._run_current_frame_until_stop()
                else:
                    self.execute_statements(func_val.body)
            except MiniStop:
                suspended = True
                raise
            except _ReturnSignal as r:
                return r.value
            finally:
                if not suspended:
                    self._pop_frame()
                    self.env = saved_env
                    if self.cooperative:
                        while self._exec_frames and self._exec_frames[-1].restore_env != saved_env:
                            self._exec_frames.pop()
                        if self._exec_frames and self._exec_frames[-1].restore_env == saved_env:
                            self._exec_frames.pop()
                else:
                    self._pop_frame()
            return None

        raise TypeError("Attempt to call a non-function value")

    def _get_lvalue(self, target: Expr) -> Any:
        if isinstance(target, Variable):
            return self.env.get(target.name)

        if isinstance(target, Lookup):
            parent = self.evaluate(target.parent)
            if isinstance(parent, OuterProxy):
                return parent.get(target.name)
            if isinstance(parent, dict):
                return parent.get(target.name, None)
            raise TypeError("Dot assignment is only supported on maps/outer")

        if isinstance(target, IndexExpr):
            if target.is_slice:
                raise RuntimeError("Slice assignment not supported")
            container = self.evaluate(target.target)
            idx = self.evaluate(target.start) if target.start is not None else None
            return self._index_get(container, idx)

        raise RuntimeError(f"Not assignable: {type(target).__name__}")

    def _set_lvalue(self, target: Expr, value: Any):
        if isinstance(target, Variable):
            self.env.set_existing_or_local(target.name, value)
            return

        if isinstance(target, Lookup):
            parent = self.evaluate(target.parent)
            if isinstance(parent, OuterProxy):
                parent.set(target.name, value)
                return
            if isinstance(parent, dict):
                parent[target.name] = value
                return
            raise TypeError("Dot assignment is only supported on maps/outer")

        if isinstance(target, IndexExpr):
            if target.is_slice:
                raise RuntimeError("Slice assignment not supported")
            container = self.evaluate(target.target)
            idx = self.evaluate(target.start) if target.start is not None else None
            self._index_set(container, idx, value)
            return

        raise RuntimeError(f"Not assignable: {type(target).__name__}")

    def _index_get(self, container: Any, idx_val: Any) -> Any:
        if idx_val is None:
            raise RuntimeError("Missing index")

        if isinstance(container, (str, list)):
            i = _as_int_index(idx_val)
            i = _normalize_index(i, len(container))
            if i < 0 or i >= len(container):
                return None
            return container[i]

        if isinstance(container, dict):
            return container.get(idx_val, None)

        raise TypeError("Indexing is supported on strings, lists, and maps")

    def _index_set(self, container: Any, idx_val: Any, value: Any):
        if idx_val is None:
            raise RuntimeError("Missing index")

        if isinstance(container, str):
            raise RuntimeError("Strings are immutable; can't assign to s[i]")

        if isinstance(container, list):
            i = _as_int_index(idx_val)
            i = _normalize_index(i, len(container))
            if i < 0 or i >= len(container):
                raise IndexError("List index out of range")
            container[i] = value
            return

        if isinstance(container, dict):
            container[idx_val] = value
            return

        raise TypeError("Index assignment is supported on lists and maps")

    def _eval_slice(self, target: Any, start_expr: Optional[Expr], end_expr: Optional[Expr]):
        if not isinstance(target, (str, list)):
            raise TypeError("Slicing is supported on strings and lists")

        n = len(target)
        start = 0
        end = n

        if start_expr is not None:
            start = _as_int_index(self.evaluate(start_expr))
            start = _normalize_index(start, n)

        if end_expr is not None:
            end = _as_int_index(self.evaluate(end_expr))
            end = _normalize_index(end, n)

        return target[start:end]

    def _apply_binary(self, left: Any, op: str, right: Any):
        if op == "and":
            return clamp01(_as_number(left) * _as_number(right))
        if op == "or":
            a = _as_number(left)
            b = _as_number(right)
            return clamp01(a + b - a * b)

        if op == "==":
            return 1.0 if left == right else 0.0
        if op == "!=":
            return 1.0 if left != right else 0.0
        if op == "<":
            return 1.0 if left < right else 0.0
        if op == ">":
            return 1.0 if left > right else 0.0
        if op == "<=":
            return 1.0 if left <= right else 0.0
        if op == ">=":
            return 1.0 if left >= right else 0.0

        if op == "^":
            return float(left) ** float(right)

        if op == "+":
            if isinstance(left, str) or isinstance(right, str):
                return miniscript_to_string(left) + miniscript_to_string(right)
            if isinstance(left, list) and isinstance(right, list):
                return left + right
            if isinstance(left, dict) and isinstance(right, dict):
                out = dict(left)
                out.update(right)
                return out
            return float(left) + float(right)

        if op == "-":
            if isinstance(left, str) and isinstance(right, str):
                return left[:-len(right)] if right != "" and left.endswith(right) else left
            return float(left) - float(right)

        if op == "*":
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                return float(left) * float(right)
            if isinstance(left, (str, list)) and isinstance(right, (int, float)):
                return _replicate_sequence(left, float(right))
            if isinstance(right, (str, list)) and isinstance(left, (int, float)):
                return _replicate_sequence(right, float(left))
            raise TypeError("Unsupported operands for *")

        if op == "/":
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                return float(left) / float(right)
            if isinstance(left, (str, list)) and isinstance(right, (int, float)):
                r = float(right)
                if r == 0:
                    raise ZeroDivisionError("division by zero")
                return _replicate_sequence(left, 1.0 / r)
            raise TypeError("Unsupported operands for /")

        if op == "%":
            return float(left) % float(right)

        raise RuntimeError(f"Unknown binary operator: {op}")


# ============================================================================
# 組み込み関数（ネイティブ実装）
# ============================================================================

def _get_self_and_shift(interp: MiniScriptInterpreter, args: List[Any]) -> Tuple[Any, List[Any]]:
    if "self" in interp.env.values:
        return interp.env.values["self"], args
    if len(args) == 0:
        return None, []
    return args[0], args[1:]


def _prepare_ms(user_code: str, context: dict = None, *, cooperative: bool = False) -> Tuple[MiniScriptInterpreter, List[Stmt]]:
    lexer = MiniScriptLexer(user_code)
    tokens = lexer.tokenize()
    parser = MiniScriptParser(tokens)
    statements = parser.parse()

    global_env = Environment(values={})
    global_env.set_local("context", context if context is not None else {})

    map_class: dict = {"_isa": None}
    number_class: dict = {"_isa": map_class}
    string_class: dict = {"_isa": map_class}
    list_class: dict = {"_isa": map_class}
    intrinsic_map_class: dict = {"_isa": map_class}

    global_env.set_local("map", intrinsic_map_class)
    global_env.set_local("list", list_class)
    global_env.set_local("string", string_class)
    global_env.set_local("number", number_class)

    interp = MiniScriptInterpreter(global_env, map_class)
    interp.cooperative = cooperative

    def _round_away_from_zero(x: float) -> float:
        ax = abs(float(x))
        return _math.copysign(_math.floor(ax + 0.5), x)

    # ---------- 数値関数 ----------
    def native_pi(interp2, args):
        return 3.14159265358979

    def native_abs(interp2, args):
        return float(abs(float(args[0] if args else 0.0)))

    def native_ceil(interp2, args):
        return float(_math.ceil(float(args[0] if args else 0.0)))

    def native_floor(interp2, args):
        return float(_math.floor(float(args[0] if args else 0.0)))

    def native_sqrt(interp2, args):
        return float(_math.sqrt(float(args[0] if args else 0.0)))

    def native_sign(interp2, args):
        x = float(args[0] if args else 0.0)
        return -1.0 if x < 0 else (1.0 if x > 0 else 0.0)

    def native_round(interp2, args):
        num = float(args[0] if len(args) > 0 else 0.0)
        decimalPlaces = int(float(args[1])) if len(args) > 1 and args[1] is not None else 0
        if decimalPlaces == 0:
            return float(_round_away_from_zero(num))
        f = _math.pow(10.0, decimalPlaces)
        return float(_round_away_from_zero(num * f) / f)

    def native_log(interp2, args):
        x = float(args[0] if len(args) > 0 else 0.0)
        base = float(args[1]) if len(args) > 1 and args[1] is not None else 10.0
        return float(_math.log(x, base))

    def native_sin(interp2, args):
        return float(_math.sin(float(args[0] if args else 0.0)))

    def native_cos(interp2, args):
        return float(_math.cos(float(args[0] if args else 0.0)))

    def native_tan(interp2, args):
        return float(_math.tan(float(args[0] if args else 0.0)))

    def native_asin(interp2, args):
        return float(_math.asin(float(args[0] if args else 0.0)))

    def native_acos(interp2, args):
        return float(_math.acos(float(args[0] if args else 0.0)))

    def native_atan(interp2, args):
        y = float(args[0] if len(args) > 0 else 0.0)
        x = float(args[1]) if len(args) > 1 and args[1] is not None else 1.0
        return float(_math.atan2(y, x))

    def native_char(interp2, args):
        i = int(float(args[0] if args else 0))
        return chr(i)

    def native_bitAnd(interp2, args):
        a = int(float(args[0] if len(args) > 0 else 0))
        b = int(float(args[1] if len(args) > 1 else 0))
        return float(a & b)

    def native_bitOr(interp2, args):
        a = int(float(args[0] if len(args) > 0 else 0))
        b = int(float(args[1] if len(args) > 1 else 0))
        return float(a | b)

    def native_bitXor(interp2, args):
        a = int(float(args[0] if len(args) > 0 else 0))
        b = int(float(args[1] if len(args) > 1 else 0))
        return float(a ^ b)

    def native_rnd(interp2, args):
        seed = args[0] if len(args) > 0 else None
        if seed is not None:
            interp2.rng.seed(int(float(seed)))
        return float(interp2.rng.random())

    def native_range(interp2, args):
        if len(args) == 0:
            return []
        if len(args) == 1:
            return _range_impl(float(args[0]), 0.0, None)
        if len(args) == 2:
            return _range_impl(float(args[0]), float(args[1]), None)
        step = None if args[2] is None else float(args[2])
        return _range_impl(float(args[0]), float(args[1]), step)

    def native_str(interp2, args):
        x = args[0] if args else None
        return miniscript_to_string(x)

    # ---------- システム関数 ----------
    def native_time(interp2, args):
        return float(_time.time() - interp2.start_time)

    def native_print(interp2, args):
        x = args[0] if len(args) > 0 else None
        delim = args[1] if len(args) > 1 else "\n"
        if delim is None:
            delim = "\n"
        interp2.write_output(miniscript_to_string(x) + str(delim))
        return None

    def native_wait(interp, args):
        t = 1.0
        if len(args) >= 1 and args[0] is not None:
            t = float(args[0])
        if t < 0:
            t = 0.0
        if interp.cooperative:
            interp._advance_after_stop()
            raise MiniWait(t)
        if t > 0:
            _time.sleep(t)
        return None

    def native_yield(interp, args):
        if interp.cooperative:
            interp._advance_after_stop()
            raise MiniYield()
        return None

    def native_refEquals(interp2, args: List[Any]):
        a = args[0] if len(args) > 0 else None
        b = args[1] if len(args) > 1 else None
        ref_types = (list, dict, FunctionValue, NativeFunctionValue, BoundMethod)
        if isinstance(a, ref_types) or isinstance(b, ref_types):
            return 1.0 if a is b else 0.0
        return 1.0 if a == b else 0.0

    def native_stackTrace(interp2, args: List[Any]):
        if "_stackAtBreak" in global_env.values:
            return global_env.values["_stackAtBreak"]
        out = []
        for loc in list(interp2.call_stack):
            if loc is None or loc.is_empty():
                continue
            out.append(loc.to_stack_string())
        return out

    def native_doString(interp2, args):
        code = str(args[0] if len(args) > 0 else "")
        if code.strip() == "":
            return None
        lexer2 = MiniScriptLexer(code)
        tokens2 = lexer2.tokenize()
        parser2 = MiniScriptParser(tokens2)
        stmts2 = parser2.parse()
        interp2.execute_statements(stmts2)
        return None

    def native_version(interp2, args):
        return "MiniScript-Python/2.0"

    def native_hash(interp2, args):
        x = args[0] if len(args) > 0 else None
        if x is None:
            return 0.0
        h = hash(miniscript_to_string(x))
        return float((h & 0x7FFFFFFF))

    # ---------- 文字列メソッド ----------
    def str_upper(interp2, args):
        self_s, _ = _get_self_and_shift(interp2, args)
        return ("" if self_s is None else str(self_s)).upper()

    def str_lower(interp2, args):
        self_s, _ = _get_self_and_shift(interp2, args)
        return ("" if self_s is None else str(self_s)).lower()

    def str_trim(interp2, args):
        self_s, _ = _get_self_and_shift(interp2, args)
        return ("" if self_s is None else str(self_s)).strip()

    def str_ltrim(interp2, args):
        self_s, _ = _get_self_and_shift(interp2, args)
        return ("" if self_s is None else str(self_s)).lstrip()

    def str_rtrim(interp2, args):
        self_s, _ = _get_self_and_shift(interp2, args)
        return ("" if self_s is None else str(self_s)).rstrip()

    def str_startsWith(interp2, args):
        self_s, a = _get_self_and_shift(interp2, args)
        s = "" if self_s is None else str(self_s)
        prefix = "" if len(a) == 0 or a[0] is None else str(a[0])
        return 1.0 if s.startswith(prefix) else 0.0

    def str_endsWith(interp2, args):
        self_s, a = _get_self_and_shift(interp2, args)
        s = "" if self_s is None else str(self_s)
        suffix = "" if len(a) == 0 or a[0] is None else str(a[0])
        return 1.0 if s.endswith(suffix) else 0.0

    def str_split(interp2, args):
        self_s, a = _get_self_and_shift(interp2, args)
        s = "" if self_s is None else str(self_s)
        delim = a[0] if len(a) > 0 else " "
        maxc = a[1] if len(a) > 1 else None
        delim = " " if delim is None else str(delim)
        maxc = None if maxc is None else _as_int_index(maxc)
        if delim == "":
            parts = list(s)
        else:
            if maxc is None:
                parts = s.split(delim)
            else:
                parts = s.split(delim, max(0, maxc - 1))
        return parts

    def str_hasIndex(interp2, args):
        self_s, a = _get_self_and_shift(interp2, args)
        s = "" if self_s is None else str(self_s)
        i = a[0] if len(a) > 0 else None
        if i is None:
            return 0.0
        i = _as_int_index(i)
        return 1.0 if 0 <= i < len(s) else 0.0

    def str_indexOf(interp2, args):
        self_s, a = _get_self_and_shift(interp2, args)
        s = "" if self_s is None else str(self_s)
        sub = "" if len(a) == 0 or a[0] is None else str(a[0])
        after = a[1] if len(a) > 1 else None
        start = 0
        if after is not None:
            start = _as_int_index(after) + 1
            if start < 0:
                start = 0
        pos = s.find(sub, start)
        return None if pos < 0 else float(pos)

    def str_insert(interp2, args):
        self_s, a = _get_self_and_shift(interp2, args)
        s = "" if self_s is None else str(self_s)
        idx = _as_int_index(a[0] if len(a) > 0 else 0)
        ins = "" if len(a) < 2 or a[1] is None else str(a[1])
        idx = _normalize_index(idx, len(s) + 1)
        if idx < 0:
            idx = 0
        if idx > len(s):
            idx = len(s)
        return s[:idx] + ins + s[idx:]

    def str_remove(interp2, args):
        self_s, a = _get_self_and_shift(interp2, args)
        s = "" if self_s is None else str(self_s)
        sub = "" if len(a) == 0 or a[0] is None else str(a[0])
        pos = s.find(sub)
        if pos < 0 or sub == "":
            return s
        return s[:pos] + s[pos + len(sub):]

    def str_replace(interp2, args):
        self_s, a = _get_self_and_shift(interp2, args)
        s = "" if self_s is None else str(self_s)
        old = "" if len(a) < 1 or a[0] is None else str(a[0])
        new = "" if len(a) < 2 or a[1] is None else str(a[1])
        maxc = a[2] if len(a) > 2 else None
        if maxc is None:
            return s.replace(old, new)
        return s.replace(old, new, _as_int_index(maxc))

    def str_val(interp2, args):
        self_s, _ = _get_self_and_shift(interp2, args)
        s = "" if self_s is None else str(self_s)
        try:
            return float(s.strip())
        except Exception:
            return 0.0

    def str_code(interp2, args):
        self_s, _ = _get_self_and_shift(interp2, args)
        s = "" if self_s is None else str(self_s)
        if len(s) == 0:
            return None
        return float(ord(s[0]))

    def global_slice(interp2, args):
        if len(args) < 1:
            return ""
        s = args[0]
        frm = args[1] if len(args) > 1 else None
        to = args[2] if len(args) > 2 else None
        if not isinstance(s, (str, list)):
            return None
        n = len(s)
        a = 0 if frm is None else _normalize_index(_as_int_index(frm), n)
        b = n if to is None else _normalize_index(_as_int_index(to), n)
        return s[a:b]

    # ---------- リストメソッド ----------
    def list_hasIndex(interp2, args):
        self_l, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            return 0.0
        i = a[0] if len(a) > 0 else None
        if i is None:
            return 0.0
        i = _as_int_index(i)
        return 1.0 if 0 <= i < len(self_l) else 0.0

    def list_hasValue(interp2, args):
        self_l, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            return 0.0
        v = a[0] if len(a) > 0 else None
        return 1.0 if v in self_l else 0.0

    def list_push(interp2, args):
        self_l, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            raise TypeError("push is only valid on lists")
        self_l.append(a[0] if len(a) > 0 else None)
        return self_l

    def list_pop(interp2, args):
        self_l, _ = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            raise TypeError("pop is only valid on lists")
        if len(self_l) == 0:
            return None
        return self_l.pop()

    def list_pull(interp2, args):
        self_l, _ = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            raise TypeError("pull is only valid on lists")
        if len(self_l) == 0:
            return None
        return self_l.pop(0)

    def list_join(interp2, args):
        self_l, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            return ""
        delim = a[0] if len(a) > 0 else " "
        delim = " " if delim is None else str(delim)
        return delim.join(miniscript_to_string(x) for x in self_l)

    def list_indexOf(interp2, args):
        self_l, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            return None
        x = a[0] if len(a) > 0 else None
        after = a[1] if len(a) > 1 else None
        start = 0
        if after is not None:
            start = _as_int_index(after) + 1
            if start < 0:
                start = 0
        for i in range(start, len(self_l)):
            if self_l[i] == x:
                return float(i)
        return None

    def list_insert(interp2, args):
        self_l, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            raise TypeError("insert is only valid on lists")
        idx = _as_int_index(a[0] if len(a) > 0 else 0)
        val = a[1] if len(a) > 1 else None
        idx = _normalize_index(idx, len(self_l) + 1)
        if idx < 0:
            idx = 0
        if idx > len(self_l):
            idx = len(self_l)
        self_l.insert(idx, val)
        return self_l

    def list_remove(interp2, args):
        self_l, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            raise TypeError("remove is only valid on lists")
        idx = _as_int_index(a[0] if len(a) > 0 else 0)
        idx = _normalize_index(idx, len(self_l))
        if idx < 0 or idx >= len(self_l):
            return self_l
        self_l.pop(idx)
        return self_l

    def list_replace(interp2, args):
        self_l, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            raise TypeError("replace is only valid on lists")
        oldv = a[0] if len(a) > 0 else None
        newv = a[1] if len(a) > 1 else None
        maxc = a[2] if len(a) > 2 else None
        limit = None if maxc is None else _as_int_index(maxc)
        count = 0
        for i in range(len(self_l)):
            if self_l[i] == oldv:
                self_l[i] = newv
                count += 1
                if limit is not None and count >= limit:
                    break
        return self_l

    def list_sum(interp2, args):
        self_l, _ = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            return 0.0
        total = 0.0
        for x in self_l:
            try:
                total += float(x)
            except Exception:
                total += 0.0
        return float(total)

    def list_sort(interp2, args):
        self_l, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            raise TypeError("sort is only valid on lists")
        key = a[0] if len(a) > 0 else None
        if key is None:
            self_l.sort(key=lambda v: miniscript_to_string(v))
        else:
            self_l.sort(key=lambda v: miniscript_to_string(v.get(key, None)) if isinstance(v, dict) else "")
        return self_l

    def list_shuffle(interp2, args):
        self_l, _ = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            raise TypeError("shuffle is only valid on lists")
        interp2.rng.shuffle(self_l)
        return self_l

    def list_reverse(interp2, args):
        self_l, _ = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            raise TypeError("reverse is only valid on lists")
        self_l.reverse()
        return self_l

    # ---------- マップメソッド ----------
    def map_hasIndex(interp2, args):
        self_m, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_m, dict):
            return 0.0
        key = a[0] if len(a) > 0 else None
        if key == "_isa":
            return 0.0
        return 1.0 if key in self_m else 0.0

    def map_hasValue(interp2, args):
        self_m, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_m, dict):
            return 0.0
        val = a[0] if len(a) > 0 else None
        for k, v in self_m.items():
            if k == "_isa":
                continue
            if v == val:
                return 1.0
        return 0.0

    def map_keys(interp2, args):
        self_m, _ = _get_self_and_shift(interp2, args)
        if not isinstance(self_m, dict):
            return []
        return [k for k in self_m.keys() if k != "_isa"]

    def map_values(interp2, args):
        self_m, _ = _get_self_and_shift(interp2, args)
        if not isinstance(self_m, dict):
            return []
        return [v for k, v in self_m.items() if k != "_isa"]

    def map_push(interp2, args):
        self_m, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_m, dict):
            raise TypeError("push is only valid on maps")
        key = a[0] if len(a) > 0 else None
        if key == "_isa":
            return self_m
        self_m[key] = 1.0
        return self_m

    def map_pop(interp2, args):
        self_m, _ = _get_self_and_shift(interp2, args)
        if not isinstance(self_m, dict):
            raise TypeError("pop is only valid on maps")
        keys = [k for k in self_m.keys() if k != "_isa"]
        if not keys:
            return None
        k = keys[0]
        self_m.pop(k, None)
        return k

    def map_remove(interp2, args):
        self_m, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_m, dict):
            raise TypeError("remove is only valid on maps")
        key = a[0] if len(a) > 0 else None
        if key == "_isa":
            return self_m
        self_m.pop(key, None)
        return self_m

    def map_indexOf(interp2, args):
        self_m, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_m, dict):
            return None
        val = a[0] if len(a) > 0 else None
        after = a[1] if len(a) > 1 else None
        keys = [k for k in self_m.keys() if k != "_isa"]
        start = 0
        if after is not None:
            try:
                start = keys.index(after) + 1
            except ValueError:
                start = 0
        for k in keys[start:]:
            if self_m.get(k, None) == val:
                return k
        return None

    def map_replace(interp2, args):
        self_m, a = _get_self_and_shift(interp2, args)
        if not isinstance(self_m, dict):
            raise TypeError("replace is only valid on maps")
        oldv = a[0] if len(a) > 0 else None
        newv = a[1] if len(a) > 1 else None
        maxc = a[2] if len(a) > 2 else None
        limit = None if maxc is None else _as_int_index(maxc)
        count = 0
        for k in list(self_m.keys()):
            if k == "_isa":
                continue
            if self_m.get(k, None) == oldv:
                self_m[k] = newv
                count += 1
                if limit is not None and count >= limit:
                    break
        return self_m

    def map_sum(interp2, args):
        self_m, _ = _get_self_and_shift(interp2, args)
        if not isinstance(self_m, dict):
            return 0.0
        total = 0.0
        for k, v in self_m.items():
            if k == "_isa":
                continue
            try:
                total += float(v)
            except Exception:
                total += 0.0
        return float(total)

    def map_shuffle(interp2, args):
        self_m, _ = _get_self_and_shift(interp2, args)
        if not isinstance(self_m, dict):
            raise TypeError("shuffle is only valid on maps")
        keys = [k for k in self_m.keys() if k != "_isa"]
        vals = [self_m[k] for k in keys]
        interp2.rng.shuffle(vals)
        for k, v in zip(keys, vals):
            self_m[k] = v
        return self_m

    # ---------- 追加メソッド ----------
    def list_clone(interp2, args):
        self_l, _ = _get_self_and_shift(interp2, args)
        if not isinstance(self_l, list):
            raise TypeError("clone is only valid on lists")
        return self_l.copy()

    def map_clone(interp2, args):
        self_m, _ = _get_self_and_shift(interp2, args)
        if not isinstance(self_m, dict):
            raise TypeError("clone is only valid on maps")
        return self_m.copy()

    def str_indexes(interp2, args):
        self_s, _ = _get_self_and_shift(interp2, args)
        return interp2.intrinsic_indexes(self_s)

    def list_indexes(interp2, args):
        self_l, _ = _get_self_and_shift(interp2, args)
        return interp2.intrinsic_indexes(self_l)

    def map_indexes(interp2, args):
        self_m, _ = _get_self_and_shift(interp2, args)
        return interp2.intrinsic_indexes(self_m)

    # ---------- 型・グローバル・ローカル関数 ----------
    def native_type(interp2, args):
        x = args[0] if args else None
        if x is None:
            return "null"
        if isinstance(x, (int, float)):
            return "number"
        if isinstance(x, str):
            return "string"
        if isinstance(x, list):
            return "list"
        if isinstance(x, dict):
            return "map"
        if isinstance(x, (FunctionValue, NativeFunctionValue, BoundMethod)):
            return "function"
        return "unknown"

    def native_globals(interp2, args):
        return interp2.global_env.values

    def native_locals(interp2, args):
        return interp2.env.values

    def native_int(interp2, args):
        x = args[0] if args else 0
        try:
            return float(_math.trunc(float(x)))
        except Exception:
            return 0.0

    def native_input(interp2, args):
        prompt = args[0] if args else ""
        sys.stdout.write(str(prompt))
        sys.stdout.flush()
        try:
            line = sys.stdin.readline()
            if line is None:
                return ""
            return line.rstrip('\n')
        except Exception:
            return ""

    # ---------- グローバルバインディング ----------
    intrinsics: Dict[str, Any] = {}

    def bind_global(name: str, fn: Callable):
        intrinsics[name] = NativeFunctionValue(fn)
        global_env.set_local(name, intrinsics[name])

    # 数値関数
    bind_global("pi", native_pi)
    bind_global("abs", native_abs)
    bind_global("ceil", native_ceil)
    bind_global("floor", native_floor)
    bind_global("sqrt", native_sqrt)
    bind_global("sign", native_sign)
    bind_global("round", native_round)
    bind_global("log", native_log)
    bind_global("sin", native_sin)
    bind_global("cos", native_cos)
    bind_global("tan", native_tan)
    bind_global("asin", native_asin)
    bind_global("acos", native_acos)
    bind_global("atan", native_atan)
    bind_global("char", native_char)
    bind_global("bitAnd", native_bitAnd)
    bind_global("bitOr", native_bitOr)
    bind_global("bitXor", native_bitXor)
    bind_global("rnd", native_rnd)
    bind_global("range", native_range)
    bind_global("str", native_str)

    # 統合関数
    bind_global("len", lambda i, a: i.intrinsic_len(a[0] if a else None))
    bind_global("indexes", lambda i, a: i.intrinsic_indexes(a[0] if a else None))
    bind_global("values", lambda i, a: i.intrinsic_values(a[0] if a else None))
    bind_global("slice", global_slice)

    # システム関数
    bind_global("time", native_time)
    bind_global("print", native_print)
    bind_global("wait", native_wait)
    bind_global("yield", native_yield)
    bind_global("refEquals", native_refEquals)
    bind_global("stackTrace", native_stackTrace)
    bind_global("doString", native_doString)
    bind_global("version", native_version)
    bind_global("hash", native_hash)

    # 拡張関数
    bind_global("type", native_type)
    bind_global("globals", native_globals)
    bind_global("locals", native_locals)
    bind_global("int", native_int)
    bind_global("input", native_input)

    global_env.set_local("intrinsics", intrinsics)
    global_env.set_local("globals", global_env.values)

    # ---------- 型クラスへのメソッドインストール ----------
    string_class["upper"] = NativeFunctionValue(str_upper)
    string_class["lower"] = NativeFunctionValue(str_lower)
    string_class["trim"] = NativeFunctionValue(str_trim)
    string_class["ltrim"] = NativeFunctionValue(str_ltrim)
    string_class["rtrim"] = NativeFunctionValue(str_rtrim)
    string_class["startsWith"] = NativeFunctionValue(str_startsWith)
    string_class["endsWith"] = NativeFunctionValue(str_endsWith)
    string_class["split"] = NativeFunctionValue(str_split)
    string_class["hasIndex"] = NativeFunctionValue(str_hasIndex)
    string_class["indexOf"] = NativeFunctionValue(str_indexOf)
    string_class["insert"] = NativeFunctionValue(str_insert)
    string_class["remove"] = NativeFunctionValue(str_remove)
    string_class["replace"] = NativeFunctionValue(str_replace)
    string_class["val"] = NativeFunctionValue(str_val)
    string_class["code"] = NativeFunctionValue(str_code)
    string_class["indexes"] = NativeFunctionValue(str_indexes)

    list_class["hasIndex"] = NativeFunctionValue(list_hasIndex)
    list_class["hasValue"] = NativeFunctionValue(list_hasValue)
    list_class["push"] = NativeFunctionValue(list_push)
    list_class["pop"] = NativeFunctionValue(list_pop)
    list_class["pull"] = NativeFunctionValue(list_pull)
    list_class["join"] = NativeFunctionValue(list_join)
    list_class["indexOf"] = NativeFunctionValue(list_indexOf)
    list_class["insert"] = NativeFunctionValue(list_insert)
    list_class["remove"] = NativeFunctionValue(list_remove)
    list_class["replace"] = NativeFunctionValue(list_replace)
    list_class["sum"] = NativeFunctionValue(list_sum)
    list_class["sort"] = NativeFunctionValue(list_sort)
    list_class["shuffle"] = NativeFunctionValue(list_shuffle)
    list_class["reverse"] = NativeFunctionValue(list_reverse)
    list_class["clone"] = NativeFunctionValue(list_clone)
    list_class["indexes"] = NativeFunctionValue(list_indexes)

    intrinsic_map_class["hasIndex"] = NativeFunctionValue(map_hasIndex)
    intrinsic_map_class["hasValue"] = NativeFunctionValue(map_hasValue)
    intrinsic_map_class["keys"] = NativeFunctionValue(map_keys)
    intrinsic_map_class["values"] = NativeFunctionValue(map_values)
    intrinsic_map_class["push"] = NativeFunctionValue(map_push)
    intrinsic_map_class["pop"] = NativeFunctionValue(map_pop)
    intrinsic_map_class["remove"] = NativeFunctionValue(map_remove)
    intrinsic_map_class["indexOf"] = NativeFunctionValue(map_indexOf)
    intrinsic_map_class["replace"] = NativeFunctionValue(map_replace)
    intrinsic_map_class["sum"] = NativeFunctionValue(map_sum)
    intrinsic_map_class["shuffle"] = NativeFunctionValue(map_shuffle)
    intrinsic_map_class["clone"] = NativeFunctionValue(map_clone)
    intrinsic_map_class["indexes"] = NativeFunctionValue(map_indexes)

    return interp, statements


# ============================================================================
# VM ドライバ
# ============================================================================

def _drive_vm_until_stop(interp: MiniScriptInterpreter, statements: List[Stmt]) -> Stop:
    if not interp._exec_frames:
        interp._exec_frames.append(_StmtFrame(statements))
    try:
        while interp._exec_frames:
            interp._run_current_frame_until_stop()
        return Stop(StopKind.DONE)
    except MiniWait as w:
        return Stop(StopKind.WAIT, w.seconds)
    except MiniYield:
        return Stop(StopKind.YIELD)


def _ms_output(interp: MiniScriptInterpreter) -> str:
    return "".join(interp.output_parts)


def _ms_translate_error(interp: MiniScriptInterpreter, e: BaseException) -> None:
    if isinstance(e, MiniStop):
        raise e
    if isinstance(e, MiniscriptException):
        raise e
    if isinstance(e, (asyncio.TimeoutError, concurrent.futures.TimeoutError)):
        raise TimeoutMsException(
            "MiniScript execution exceeded the timeout limit",
            interp.current_sourceloc()
        ) from e
    if isinstance(e, NameError):
        msg = str(e)
        name = None
        if "'" in msg:
            parts = msg.split("'")
            if len(parts) >= 2:
                name = parts[1]
        if name:
            raise RuntimeMsException(
                f"Undefined Identifier: '{name}' is unknown in this context",
                interp.current_sourceloc()
            ) from e
        raise RuntimeMsException(msg, interp.current_sourceloc()) from e
    if isinstance(e, Exception):
        raise RuntimeMsException(str(e), interp.current_sourceloc()) from e
    raise e


def _ms_run_sync(user_code: str, context: dict = None) -> str:
    interp, statements = _prepare_ms(user_code, context, cooperative=False)
    try:
        interp.execute_statements(statements)
        return _ms_output(interp)
    except BaseException as e:
        _ms_translate_error(interp, e)
        return ""


# ============================================================================
# 公開API
# ============================================================================

def ms(user_code: str, context: dict = None, timeout: float | None = None) -> str:
    """MiniScript を同期実行（wait/yield は time.sleep）。"""
    if timeout is None:
        return _ms_run_sync(user_code, context)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_ms_run_sync, user_code, context)
        try:
            return fut.result(timeout=float(timeout))
        except concurrent.futures.TimeoutError as e:
            raise TimeoutMsException(
                f"MiniScript execution exceeded {timeout} seconds"
            ) from e


async def ams(user_code: str, context: dict = None, timeout: float | None = None) -> str:
    """MiniScript を非同期実行（wait/yield は asyncio.sleep）。"""
    async def _run() -> str:
        interp, statements = _prepare_ms(user_code, context, cooperative=True)
        try:
            while True:
                stop = _drive_vm_until_stop(interp, statements)
                if stop.kind == StopKind.DONE:
                    return _ms_output(interp)
                if stop.kind == StopKind.WAIT:
                    await asyncio.sleep(stop.seconds)
                elif stop.kind == StopKind.YIELD:
                    await asyncio.sleep(0)
        except BaseException as e:
            _ms_translate_error(interp, e)
            return ""

    if timeout is None:
        return await _run()
    try:
        return await asyncio.wait_for(_run(), timeout=float(timeout))
    except asyncio.TimeoutError as e:
        raise TimeoutMsException(
            f"MiniScript execution exceeded {timeout} seconds"
        ) from e


# ============================================================================
# テスト（このファイルが直接実行された時のみ）
# ============================================================================

if __name__ == "__main__":
    async def _async_test():
        script = """
s = "hello"
print s.indexes

lst = [10,20,30]
print lst.indexes

m = {"a":1, "b":2}
print m.indexes
"""
        print("--- async test ---")
        out = await ams(script, timeout=5.0)
        print(out)

    def _sync_test():
        script = """
s = "hello"
print s.indexes

lst = [10,20,30]
print lst.indexes

m = {"a":1, "b":2}
print m.indexes
"""
        print("--- sync test ---")
        out = ms(script)
        print(out)

    try:
        _sync_test()
        print()
        asyncio.run(_async_test())
    except KeyboardInterrupt:
        pass