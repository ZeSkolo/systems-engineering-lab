"""SQL lexer + recursive-descent parser -> query plan nodes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

KEYWORDS = {
    "CREATE",
    "TABLE",
    "INSERT",
    "INTO",
    "VALUES",
    "SELECT",
    "FROM",
    "WHERE",
    "DELETE",
    "DROP",
    "INT",
    "INTEGER",
    "TEXT",
    "AND",
    "OR",
    "ORDER",
    "BY",
    "LIMIT",
    "NULL",
    "STAR",
}


@dataclass
class Token:
    kind: str
    value: Any
    pos: int


class Lexer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.i = 0

    def tokens(self) -> list[Token]:
        out: list[Token] = []
        while True:
            t = self._next()
            out.append(t)
            if t.kind == "EOF":
                return out

    def _next(self) -> Token:
        s = self.text
        n = len(s)
        while self.i < n and s[self.i].isspace():
            self.i += 1
        if self.i >= n:
            return Token("EOF", None, self.i)
        start = self.i
        c = s[self.i]
        if c in ",()*":
            self.i += 1
            return Token(c, c, start)
        if c == "'" or c == '"':
            quote = c
            self.i += 1
            buf = []
            while self.i < n and s[self.i] != quote:
                if s[self.i] == "\\" and self.i + 1 < n:
                    self.i += 1
                buf.append(s[self.i])
                self.i += 1
            if self.i >= n:
                raise SyntaxError("unterminated string")
            self.i += 1
            return Token("STRING", "".join(buf), start)
        two = s[self.i : self.i + 2]
        if two in {"<=", ">=", "!=", "<>"}:
            self.i += 2
            return Token("OP", "!=" if two == "<>" else two, start)
        if c in "=<>":
            self.i += 1
            return Token("OP", c, start)
        if c == ";":
            self.i += 1
            return Token(";", ";", start)
        if c.isdigit() or (c == "-" and self.i + 1 < n and s[self.i + 1].isdigit()):
            if c == "-":
                self.i += 1
            while self.i < n and s[self.i].isdigit():
                self.i += 1
            return Token("NUMBER", int(s[start : self.i]), start)
        if c.isalpha() or c == "_":
            while self.i < n and (s[self.i].isalnum() or s[self.i] == "_"):
                self.i += 1
            word = s[start : self.i]
            up = word.upper()
            if up in KEYWORDS:
                return Token(up, up, start)
            return Token("IDENT", word, start)
        raise SyntaxError(f"unexpected character {c!r} at {start}")


@dataclass
class ColumnDef:
    name: str
    col_type: str  # INT | TEXT


@dataclass
class CreateTable:
    name: str
    columns: list[ColumnDef]


@dataclass
class Insert:
    table: str
    columns: list[str] | None
    values: list[Any]


@dataclass
class Predicate:
    column: str
    op: str
    value: Any


@dataclass
class Select:
    table: str
    columns: list[str] | None  # None = *
    where: list[Predicate] = field(default_factory=list)
    order_by: str | None = None
    limit: int | None = None


@dataclass
class Delete:
    table: str
    where: list[Predicate] = field(default_factory=list)


@dataclass
class DropTable:
    name: str


class Parser:
    def __init__(self, text: str) -> None:
        self.toks = Lexer(text).tokens()
        self.i = 0

    def peek(self) -> Token:
        return self.toks[self.i]

    def eat(self, kind: str | None = None, value: Any = None) -> Token:
        t = self.peek()
        if kind and t.kind != kind:
            raise SyntaxError(f"expected {kind}, got {t.kind} ({t.value!r})")
        if value is not None and t.value != value:
            raise SyntaxError(f"expected {value}, got {t.value}")
        self.i += 1
        return t

    def parse(self):
        t = self.peek()
        if t.kind == "CREATE":
            stmt = self.create_table()
        elif t.kind == "INSERT":
            stmt = self.insert()
        elif t.kind == "SELECT":
            stmt = self.select()
        elif t.kind == "DELETE":
            stmt = self.delete()
        elif t.kind == "DROP":
            stmt = self.drop()
        else:
            raise SyntaxError(f"unknown statement starting with {t.kind}")
        if self.peek().kind == ";":
            self.eat(";")
        return stmt

    def create_table(self) -> CreateTable:
        self.eat("CREATE")
        self.eat("TABLE")
        name = self.eat("IDENT").value
        self.eat("(")
        cols = [self.col_def()]
        while self.peek().kind == ",":
            self.eat(",")
            cols.append(self.col_def())
        self.eat(")")
        return CreateTable(name, cols)

    def col_def(self) -> ColumnDef:
        name = self.eat("IDENT").value
        t = self.peek()
        if t.kind in {"INT", "INTEGER"}:
            self.eat()
            return ColumnDef(name, "INT")
        if t.kind == "TEXT":
            self.eat()
            return ColumnDef(name, "TEXT")
        raise SyntaxError(f"expected type for column {name}")

    def insert(self) -> Insert:
        self.eat("INSERT")
        self.eat("INTO")
        table = self.eat("IDENT").value
        columns = None
        if self.peek().kind == "(":
            self.eat("(")
            columns = [self.eat("IDENT").value]
            while self.peek().kind == ",":
                self.eat(",")
                columns.append(self.eat("IDENT").value)
            self.eat(")")
        self.eat("VALUES")
        self.eat("(")
        values = [self.literal()]
        while self.peek().kind == ",":
            self.eat(",")
            values.append(self.literal())
        self.eat(")")
        return Insert(table, columns, values)

    def select(self) -> Select:
        self.eat("SELECT")
        cols = None
        if self.peek().kind == "*":
            self.eat("*")
        else:
            cols = [self.eat("IDENT").value]
            while self.peek().kind == ",":
                self.eat(",")
                cols.append(self.eat("IDENT").value)
        self.eat("FROM")
        table = self.eat("IDENT").value
        where = self.where()
        order_by = None
        if self.peek().kind == "ORDER":
            self.eat("ORDER")
            self.eat("BY")
            order_by = self.eat("IDENT").value
        limit = None
        if self.peek().kind == "LIMIT":
            self.eat("LIMIT")
            limit = int(self.eat("NUMBER").value)
        return Select(table, cols, where, order_by, limit)

    def delete(self) -> Delete:
        self.eat("DELETE")
        self.eat("FROM")
        table = self.eat("IDENT").value
        return Delete(table, self.where())

    def drop(self) -> DropTable:
        self.eat("DROP")
        self.eat("TABLE")
        return DropTable(self.eat("IDENT").value)

    def where(self) -> list[Predicate]:
        if self.peek().kind != "WHERE":
            return []
        self.eat("WHERE")
        preds = [self.predicate()]
        while self.peek().kind == "AND":
            self.eat("AND")
            preds.append(self.predicate())
        return preds

    def predicate(self) -> Predicate:
        col = self.eat("IDENT").value
        op = self.eat("OP").value
        val = self.literal()
        return Predicate(col, op, val)

    def literal(self) -> Any:
        t = self.peek()
        if t.kind == "NUMBER":
            self.eat()
            return int(t.value)
        if t.kind == "STRING":
            self.eat()
            return str(t.value)
        if t.kind == "NULL":
            self.eat()
            return None
        raise SyntaxError(f"expected literal, got {t.kind}")


def parse_sql(text: str):
    return Parser(text).parse()
