/* minic — subset C compiler to x86-64 AT&T assembly (stdout).
 *
 * Stages: lexer -> recursive-descent parser/AST -> types/offsets -> codegen.
 * Subset: int, pointers, arrays, functions (<=6 args), if/else, while, for,
 *         arithmetic, comparisons, && || !, sizeof, comments.
 *
 *   ./minic examples/fact.c > fact.s && gcc -static -o fact fact.s && ./fact; echo $?
 */
#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct Token Token;
typedef struct Node Node;
typedef struct Type Type;
typedef struct Obj Obj;

static char *current_file;
static char *user_input;
static int label_id;

static void error(char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vfprintf(stderr, fmt, ap);
  fprintf(stderr, "\n");
  va_end(ap);
  exit(1);
}

/* ---------- types ---------- */
typedef enum { TY_INT, TY_PTR, TY_FUNC, TY_ARRAY } TypeKind;
struct Type {
  TypeKind kind;
  int size;
  Type *base;
  Type *return_ty;
};
static Type *ty_int = &(Type){TY_INT, 8, NULL, NULL};

static Type *pointer_to(Type *base) {
  Type *ty = calloc(1, sizeof(Type));
  ty->kind = TY_PTR;
  ty->size = 8;
  ty->base = base;
  return ty;
}
static Type *array_of(Type *base, int len) {
  Type *ty = calloc(1, sizeof(Type));
  ty->kind = TY_ARRAY;
  ty->size = base->size * len;
  ty->base = base;
  return ty;
}
static Type *func_type(Type *ret) {
  Type *ty = calloc(1, sizeof(Type));
  ty->kind = TY_FUNC;
  ty->return_ty = ret;
  ty->size = 8;
  return ty;
}

/* ---------- tokens ---------- */
typedef enum { TK_IDENT, TK_PUNCT, TK_KEYWORD, TK_NUM, TK_EOF } TokenKind;
struct Token {
  TokenKind kind;
  Token *next;
  int val;
  char *loc;
  int len;
};

static bool equal(Token *tok, char *op) {
  return (int)strlen(op) == tok->len && memcmp(tok->loc, op, tok->len) == 0;
}
static Token *skip(Token *tok, char *op) {
  if (!equal(tok, op))
    error("expected '%s' near '%.10s'", op, tok->loc);
  return tok->next;
}
static Token *new_token(TokenKind kind, char *start, char *end) {
  Token *tok = calloc(1, sizeof(Token));
  tok->kind = kind;
  tok->loc = start;
  tok->len = (int)(end - start);
  return tok;
}
static bool starts_with(char *p, char *q) { return strncmp(p, q, strlen(q)) == 0; }
static bool is_ident1(char c) { return isalpha((unsigned char)c) || c == '_'; }
static bool is_ident2(char c) { return is_ident1(c) || isdigit((unsigned char)c); }
static int read_punct(char *p) {
  static char *ops[] = {"==", "!=", "<=", ">=", "&&", "||"};
  for (int i = 0; i < 6; i++)
    if (starts_with(p, ops[i]))
      return (int)strlen(ops[i]);
  return ispunct((unsigned char)*p) && *p != '"' ? 1 : 0;
}
static bool is_keyword(Token *tok) {
  static char *kw[] = {"return", "if", "else", "while", "for", "int", "sizeof", NULL};
  for (int i = 0; kw[i]; i++)
    if (equal(tok, kw[i]))
      return true;
  return false;
}
static Token *tokenize(char *p) {
  Token head = {0};
  Token *cur = &head;
  while (*p) {
    if (isspace((unsigned char)*p)) { p++; continue; }
    if (starts_with(p, "//")) { while (*p && *p != '\n') p++; continue; }
    if (starts_with(p, "/*")) {
      char *q = strstr(p + 2, "*/");
      if (!q) error("unclosed comment");
      p = q + 2;
      continue;
    }
    if (isdigit((unsigned char)*p)) {
      char *start = p;
      cur = cur->next = new_token(TK_NUM, start, start);
      cur->val = (int)strtol(p, &p, 10);
      cur->len = (int)(p - start);
      continue;
    }
    if (is_ident1(*p)) {
      char *start = p;
      do { p++; } while (is_ident2(*p));
      cur = cur->next = new_token(TK_IDENT, start, p);
      continue;
    }
    int n = read_punct(p);
    if (n) { cur = cur->next = new_token(TK_PUNCT, p, p + n); p += n; continue; }
    error("invalid token near '%.10s'", p);
  }
  cur = cur->next = new_token(TK_EOF, p, p);
  for (Token *t = head.next; t; t = t->next)
    if (t->kind == TK_IDENT && is_keyword(t))
      t->kind = TK_KEYWORD;
  return head.next;
}

/* ---------- AST / objects ---------- */
typedef enum {
  ND_ADD, ND_SUB, ND_MUL, ND_DIV, ND_NEG, ND_EQ, ND_NE, ND_LT, ND_LE,
  ND_ASSIGN, ND_ADDR, ND_DEREF, ND_NOT, ND_LOGAND, ND_LOGOR,
  ND_RETURN, ND_IF, ND_FOR, ND_BLOCK, ND_FUNCALL, ND_EXPR_STMT, ND_VAR, ND_NUM
} NodeKind;

struct Node {
  NodeKind kind;
  Node *next;
  Type *ty;
  Token *tok;
  Node *lhs, *rhs;
  Node *cond, *then, *els, *init, *inc, *body, *args;
  Obj *var;
  char *funcname;
  int val;
};
struct Obj {
  Obj *next;
  Obj *param_next;
  char *name;
  Type *ty;
  bool is_local, is_function;
  int offset, stack_size;
  Obj *params;
  Obj *locals;
  Node *body;
};

static Obj *locals;
static Obj *globals;

static char *strndup_(char *p, int len) {
  char *b = calloc(1, len + 1);
  memcpy(b, p, len);
  return b;
}
static char *get_ident(Token *tok) {
  if (tok->kind != TK_IDENT)
    error("expected identifier near '%.10s'", tok->loc);
  return strndup_(tok->loc, tok->len);
}
static Obj *find_var(Token *tok) {
  for (Obj *v = locals; v; v = v->next)
    if ((int)strlen(v->name) == tok->len && !memcmp(tok->loc, v->name, tok->len))
      return v;
  for (Obj *v = globals; v; v = v->next)
    if ((int)strlen(v->name) == tok->len && !memcmp(tok->loc, v->name, tok->len))
      return v;
  return NULL;
}
static Node *new_node(NodeKind kind, Token *tok) {
  Node *n = calloc(1, sizeof(Node));
  n->kind = kind;
  n->tok = tok;
  return n;
}
static Node *new_binary(NodeKind k, Node *lhs, Node *rhs, Token *tok) {
  Node *n = new_node(k, tok);
  n->lhs = lhs;
  n->rhs = rhs;
  return n;
}
static Node *new_unary(NodeKind k, Node *expr, Token *tok) {
  Node *n = new_node(k, tok);
  n->lhs = expr;
  return n;
}
static Node *new_num(int val, Token *tok) {
  Node *n = new_node(ND_NUM, tok);
  n->val = val;
  n->ty = ty_int;
  return n;
}
static Node *new_var_node(Obj *var, Token *tok) {
  Node *n = new_node(ND_VAR, tok);
  n->var = var;
  return n;
}
static Obj *new_lvar(char *name, Type *ty) {
  Obj *v = calloc(1, sizeof(Obj));
  v->name = name;
  v->ty = ty;
  v->is_local = true;
  v->next = locals;
  locals = v;
  return v;
}

/* ---------- typing ---------- */
static void add_type(Node *node);
static Node *new_add(Node *lhs, Node *rhs, Token *tok);
static Node *new_sub(Node *lhs, Node *rhs, Token *tok);

static void add_type(Node *node) {
  if (!node || node->ty) return;
  add_type(node->lhs); add_type(node->rhs);
  add_type(node->cond); add_type(node->then); add_type(node->els);
  add_type(node->init); add_type(node->inc); add_type(node->body);
  for (Node *n = node->next; n; n = n->next) add_type(n);
  for (Node *n = node->args; n; n = n->next) add_type(n);
  switch (node->kind) {
  case ND_ADD:
  case ND_SUB:
  case ND_MUL:
  case ND_DIV:
  case ND_NEG:
  case ND_EQ:
  case ND_NE:
  case ND_LT:
  case ND_LE:
  case ND_NUM:
  case ND_NOT:
  case ND_LOGAND:
  case ND_LOGOR:
  case ND_FUNCALL:
    node->ty = ty_int; return;
  case ND_ASSIGN:
    node->ty = node->lhs->ty; return;
  case ND_VAR:
    node->ty = node->var->ty; return;
  case ND_ADDR:
    if (node->lhs->ty->kind == TY_ARRAY)
      node->ty = pointer_to(node->lhs->ty->base);
    else
      node->ty = pointer_to(node->lhs->ty);
    return;
  case ND_DEREF:
    if (!node->lhs->ty->base) error("invalid pointer dereference");
    node->ty = node->lhs->ty->base; return;
  default:
    return;
  }
}

static Node *new_add(Node *lhs, Node *rhs, Token *tok) {
  add_type(lhs); add_type(rhs);
  if (lhs->ty->kind == TY_INT && rhs->ty->kind == TY_INT)
    return new_binary(ND_ADD, lhs, rhs, tok);
  if (lhs->ty->base && rhs->ty->kind == TY_INT)
    return new_binary(ND_ADD, lhs, new_binary(ND_MUL, rhs, new_num(lhs->ty->base->size, tok), tok), tok);
  if (lhs->ty->kind == TY_INT && rhs->ty->base)
    return new_binary(ND_ADD, rhs, new_binary(ND_MUL, lhs, new_num(rhs->ty->base->size, tok), tok), tok);
  error("invalid operands for +");
  return NULL;
}
static Node *new_sub(Node *lhs, Node *rhs, Token *tok) {
  add_type(lhs); add_type(rhs);
  if (lhs->ty->kind == TY_INT && rhs->ty->kind == TY_INT)
    return new_binary(ND_SUB, lhs, rhs, tok);
  if (lhs->ty->base && rhs->ty->kind == TY_INT)
    return new_binary(ND_SUB, lhs, new_binary(ND_MUL, rhs, new_num(lhs->ty->base->size, tok), tok), tok);
  error("invalid operands for -");
  return NULL;
}

/* ---------- parser ---------- */
static Node *stmt(Token **rest, Token *tok);
static Node *compound_stmt(Token **rest, Token *tok);
static Node *expr(Token **rest, Token *tok);
static Node *assign(Token **rest, Token *tok);
static Node *logor(Token **rest, Token *tok);
static Node *logand(Token **rest, Token *tok);
static Node *equality(Token **rest, Token *tok);
static Node *relational(Token **rest, Token *tok);
static Node *add_(Token **rest, Token *tok);
static Node *mul(Token **rest, Token *tok);
static Node *unary(Token **rest, Token *tok);
static Node *postfix(Token **rest, Token *tok);
static Node *primary(Token **rest, Token *tok);

static Node *expr(Token **rest, Token *tok) { return assign(rest, tok); }

static Node *assign(Token **rest, Token *tok) {
  Node *n = logor(&tok, tok);
  if (equal(tok, "=")) {
    Node *rhs = assign(&tok, tok->next);
    n = new_binary(ND_ASSIGN, n, rhs, tok);
  }
  *rest = tok;
  return n;
}
static Node *logor(Token **rest, Token *tok) {
  Node *n = logand(&tok, tok);
  while (equal(tok, "||")) {
    Token *op = tok;
    n = new_binary(ND_LOGOR, n, logand(&tok, tok->next), op);
  }
  *rest = tok; return n;
}
static Node *logand(Token **rest, Token *tok) {
  Node *n = equality(&tok, tok);
  while (equal(tok, "&&")) {
    Token *op = tok;
    n = new_binary(ND_LOGAND, n, equality(&tok, tok->next), op);
  }
  *rest = tok; return n;
}
static Node *equality(Token **rest, Token *tok) {
  Node *n = relational(&tok, tok);
  for (;;) {
    Token *op = tok;
    if (equal(tok, "==")) n = new_binary(ND_EQ, n, relational(&tok, tok->next), op);
    else if (equal(tok, "!=")) n = new_binary(ND_NE, n, relational(&tok, tok->next), op);
    else { *rest = tok; return n; }
  }
}
static Node *relational(Token **rest, Token *tok) {
  Node *n = add_(&tok, tok);
  for (;;) {
    Token *op = tok;
    if (equal(tok, "<")) n = new_binary(ND_LT, n, add_(&tok, tok->next), op);
    else if (equal(tok, "<=")) n = new_binary(ND_LE, n, add_(&tok, tok->next), op);
    else if (equal(tok, ">")) n = new_binary(ND_LT, add_(&tok, tok->next), n, op);
    else if (equal(tok, ">=")) n = new_binary(ND_LE, add_(&tok, tok->next), n, op);
    else { *rest = tok; return n; }
  }
}
static Node *add_(Token **rest, Token *tok) {
  Node *n = mul(&tok, tok);
  for (;;) {
    Token *op = tok;
    if (equal(tok, "+")) n = new_add(n, mul(&tok, tok->next), op);
    else if (equal(tok, "-")) n = new_sub(n, mul(&tok, tok->next), op);
    else { *rest = tok; return n; }
  }
}
static Node *mul(Token **rest, Token *tok) {
  Node *n = unary(&tok, tok);
  for (;;) {
    Token *op = tok;
    if (equal(tok, "*")) n = new_binary(ND_MUL, n, unary(&tok, tok->next), op);
    else if (equal(tok, "/")) n = new_binary(ND_DIV, n, unary(&tok, tok->next), op);
    else { *rest = tok; return n; }
  }
}
static Node *unary(Token **rest, Token *tok) {
  if (equal(tok, "+")) return unary(rest, tok->next);
  if (equal(tok, "-")) return new_unary(ND_NEG, unary(rest, tok->next), tok);
  if (equal(tok, "&")) return new_unary(ND_ADDR, unary(rest, tok->next), tok);
  if (equal(tok, "*")) return new_unary(ND_DEREF, unary(rest, tok->next), tok);
  if (equal(tok, "!")) return new_unary(ND_NOT, unary(rest, tok->next), tok);
  if (equal(tok, "sizeof")) {
    Node *n = unary(rest, tok->next);
    add_type(n);
    return new_num(n->ty->kind == TY_ARRAY ? n->ty->size : n->ty->size, tok);
  }
  return postfix(rest, tok);
}
static Node *postfix(Token **rest, Token *tok) {
  Node *n = primary(&tok, tok);
  while (equal(tok, "[")) {
    Token *op = tok;
    Node *idx = expr(&tok, tok->next);
    tok = skip(tok, "]");
    n = new_unary(ND_DEREF, new_add(n, idx, op), op);
  }
  *rest = tok; return n;
}
static Node *primary(Token **rest, Token *tok) {
  if (equal(tok, "(")) {
    Node *n = expr(&tok, tok->next);
    *rest = skip(tok, ")");
    return n;
  }
  if (tok->kind == TK_NUM) {
    Node *n = new_num(tok->val, tok);
    *rest = tok->next;
    return n;
  }
  if (tok->kind == TK_IDENT && equal(tok->next, "(")) {
    Node *node = new_node(ND_FUNCALL, tok);
    node->funcname = get_ident(tok);
    tok = tok->next->next;
    Node head = {0};
    Node *cur = &head;
    while (!equal(tok, ")")) {
      if (cur != &head) tok = skip(tok, ",");
      cur = cur->next = assign(&tok, tok);
    }
    node->args = head.next;
    *rest = skip(tok, ")");
    return node;
  }
  if (tok->kind == TK_IDENT) {
    Obj *var = find_var(tok);
    if (!var) error("undefined variable '%.*s'", tok->len, tok->loc);
    *rest = tok->next;
    return new_var_node(var, tok);
  }
  error("expected expression near '%.10s'", tok->loc);
  return NULL;
}

static Node *stmt(Token **rest, Token *tok) {
  if (equal(tok, "return")) {
    Node *n = new_unary(ND_RETURN, expr(&tok, tok->next), tok);
    *rest = skip(tok, ";");
    return n;
  }
  if (equal(tok, "if")) {
    Node *n = new_node(ND_IF, tok);
    tok = skip(tok->next, "(");
    n->cond = expr(&tok, tok);
    tok = skip(tok, ")");
    n->then = stmt(&tok, tok);
    if (equal(tok, "else"))
      n->els = stmt(&tok, tok->next);
    *rest = tok;
    return n;
  }
  if (equal(tok, "while")) {
    Node *n = new_node(ND_FOR, tok);
    tok = skip(tok->next, "(");
    n->cond = expr(&tok, tok);
    tok = skip(tok, ")");
    n->then = stmt(&tok, tok);
    *rest = tok;
    return n;
  }
  if (equal(tok, "for")) {
    Node *n = new_node(ND_FOR, tok);
    tok = skip(tok->next, "(");
    if (!equal(tok, ";")) n->init = expr(&tok, tok);
    tok = skip(tok, ";");
    if (!equal(tok, ";")) n->cond = expr(&tok, tok);
    tok = skip(tok, ";");
    if (!equal(tok, ")")) n->inc = expr(&tok, tok);
    tok = skip(tok, ")");
    n->then = stmt(&tok, tok);
    *rest = tok;
    return n;
  }
  if (equal(tok, "{"))
    return compound_stmt(rest, tok);
  if (equal(tok, "int")) {
    Type *ty = ty_int;
    tok = tok->next;
    while (equal(tok, "*")) { ty = pointer_to(ty); tok = tok->next; }
    char *name = get_ident(tok);
    tok = tok->next;
    if (equal(tok, "[")) {
      tok = tok->next;
      if (tok->kind != TK_NUM) error("array length must be a number");
      ty = array_of(ty, tok->val);
      tok = skip(tok->next, "]");
    }
    Obj *var = new_lvar(name, ty);
    Node *n;
    if (equal(tok, "=")) {
      Token *op = tok;
      n = new_unary(ND_EXPR_STMT, new_binary(ND_ASSIGN, new_var_node(var, op), expr(&tok, tok->next), op), op);
    } else {
      n = new_node(ND_BLOCK, tok);
    }
    *rest = skip(tok, ";");
    return n;
  }
  if (equal(tok, ";")) {
    *rest = tok->next;
    return new_node(ND_BLOCK, tok);
  }
  Node *n = new_unary(ND_EXPR_STMT, expr(&tok, tok), tok);
  *rest = skip(tok, ";");
  return n;
}

static Node *compound_stmt(Token **rest, Token *tok) {
  Node *node = new_node(ND_BLOCK, tok);
  tok = skip(tok, "{");
  Node head = {0};
  Node *cur = &head;
  while (!equal(tok, "}"))
    cur = cur->next = stmt(&tok, tok);
  node->body = head.next;
  *rest = tok->next;
  return node;
}

static int align_to(int n, int align) { return (n + align - 1) / align * align; }

static void assign_lvar_offsets(Obj *fn) {
  int offset = 0;
  for (Obj *var = fn->locals; var; var = var->next) {
    offset += var->ty->size ? var->ty->size : 8;
    offset = align_to(offset, 8);
    var->offset = offset;
  }
  fn->stack_size = align_to(offset, 16);
}

static Obj *parse(Token *tok) {
  globals = NULL;
  while (tok->kind != TK_EOF) {
    tok = skip(tok, "int");
    Type *ty = ty_int;
    while (equal(tok, "*")) { ty = pointer_to(ty); tok = tok->next; }
    char *name = get_ident(tok);
    tok = tok->next;
    if (!equal(tok, "(")) error("only function definitions are supported at file scope");
    locals = NULL;
    Obj *fn = calloc(1, sizeof(Obj));
    fn->name = name;
    fn->is_function = true;
    fn->ty = func_type(ty);
    tok = skip(tok, "(");
    Obj *ptail = NULL;
    int n = 0;
    while (!equal(tok, ")")) {
      if (n++) tok = skip(tok, ",");
      tok = skip(tok, "int");
      Type *pty = ty_int;
      while (equal(tok, "*")) { pty = pointer_to(pty); tok = tok->next; }
      Obj *var = new_lvar(get_ident(tok), pty);
      tok = tok->next;
      if (!fn->params) fn->params = var;
      else ptail->param_next = var;
      ptail = var;
    }
    tok = skip(tok, ")");
    fn->body = compound_stmt(&tok, tok);
    fn->locals = locals;
    assign_lvar_offsets(fn);
    fn->next = globals;
    globals = fn;
  }
  return globals;
}

/* ---------- codegen ---------- */
static char *argreg[] = {"%rdi", "%rsi", "%rdx", "%rcx", "%r8", "%r9"};
static char *current_fn;

static void gen_expr(Node *node);
static void gen_stmt(Node *node);

static void push(void) { printf("  push %%rax\n"); }
static void pop(char *arg) { printf("  pop %s\n", arg); }

static void gen_addr(Node *node) {
  switch (node->kind) {
  case ND_VAR:
    if (node->var->ty->kind == TY_ARRAY)
      printf("  lea %d(%%rbp), %%rax\n", -node->var->offset);
    else if (node->var->is_local)
      printf("  lea %d(%%rbp), %%rax\n", -node->var->offset);
    else
      printf("  lea %s(%%rip), %%rax\n", node->var->name);
    return;
  case ND_DEREF:
    gen_expr(node->lhs);
    return;
  default:
    error("not an lvalue");
  }
}

static void gen_expr(Node *node) {
  add_type(node);
  int l, r;
  switch (node->kind) {
  case ND_NUM:
    printf("  mov $%d, %%rax\n", node->val);
    return;
  case ND_NEG:
    gen_expr(node->lhs);
    printf("  neg %%rax\n");
    return;
  case ND_VAR:
    if (node->var->ty->kind == TY_ARRAY) { gen_addr(node); return; }
    gen_addr(node);
    printf("  mov (%%rax), %%rax\n");
    return;
  case ND_DEREF:
    gen_expr(node->lhs);
    printf("  mov (%%rax), %%rax\n");
    return;
  case ND_ADDR:
    gen_addr(node->lhs);
    return;
  case ND_NOT:
    gen_expr(node->lhs);
    printf("  cmp $0, %%rax\n  sete %%al\n  movzb %%al, %%rax\n");
    return;
  case ND_ASSIGN:
    gen_addr(node->lhs);
    push();
    gen_expr(node->rhs);
    pop("%rdi");
    printf("  mov %%rax, (%%rdi)\n");
    return;
  case ND_LOGAND:
    l = label_id++;
    r = label_id++;
    gen_expr(node->lhs);
    printf("  cmp $0, %%rax\n  je .L.false.%d\n", l);
    gen_expr(node->rhs);
    printf("  cmp $0, %%rax\n  je .L.false.%d\n  mov $1, %%rax\n  jmp .L.end.%d\n.L.false.%d:\n  mov $0, %%rax\n.L.end.%d:\n",
           l, r, l, r);
    return;
  case ND_LOGOR:
    l = label_id++;
    r = label_id++;
    gen_expr(node->lhs);
    printf("  cmp $0, %%rax\n  jne .L.true.%d\n", l);
    gen_expr(node->rhs);
    printf("  cmp $0, %%rax\n  jne .L.true.%d\n  mov $0, %%rax\n  jmp .L.end.%d\n.L.true.%d:\n  mov $1, %%rax\n.L.end.%d:\n",
           l, r, l, r);
    return;
  case ND_FUNCALL: {
    int nargs = 0;
    for (Node *a = node->args; a; a = a->next) nargs++;
    int pad = nargs % 2;
    if (pad) printf("  sub $8, %%rsp\n");
    for (Node *a = node->args; a; a = a->next) { gen_expr(a); push(); }
    for (int i = nargs - 1; i >= 0; i--) pop(argreg[i]);
    printf("  mov $0, %%rax\n  call %s\n", node->funcname);
    if (pad) printf("  add $8, %%rsp\n");
    return;
  }
  default:
    break;
  }
  gen_expr(node->rhs);
  push();
  gen_expr(node->lhs);
  pop("%rdi");
  switch (node->kind) {
  case ND_ADD: printf("  add %%rdi, %%rax\n"); return;
  case ND_SUB: printf("  sub %%rdi, %%rax\n"); return;
  case ND_MUL: printf("  imul %%rdi, %%rax\n"); return;
  case ND_DIV: printf("  cqo\n  idiv %%rdi\n"); return;
  case ND_EQ:
  case ND_NE:
  case ND_LT:
  case ND_LE:
    printf("  cmp %%rdi, %%rax\n");
    if (node->kind == ND_EQ) printf("  sete %%al\n");
    else if (node->kind == ND_NE) printf("  setne %%al\n");
    else if (node->kind == ND_LT) printf("  setl %%al\n");
    else printf("  setle %%al\n");
    printf("  movzb %%al, %%rax\n");
    return;
  default:
    error("invalid expression");
  }
}

static void gen_stmt(Node *node) {
  int l, r;
  switch (node->kind) {
  case ND_IF:
    l = label_id++; r = label_id++;
    gen_expr(node->cond);
    printf("  cmp $0, %%rax\n  je .L.else.%d\n", l);
    gen_stmt(node->then);
    printf("  jmp .L.end.%d\n.L.else.%d:\n", r, l);
    if (node->els) gen_stmt(node->els);
    printf(".L.end.%d:\n", r);
    return;
  case ND_FOR:
    l = label_id++; r = label_id++;
    if (node->init) gen_expr(node->init);
    printf(".L.begin.%d:\n", l);
    if (node->cond) {
      gen_expr(node->cond);
      printf("  cmp $0, %%rax\n  je .L.end.%d\n", r);
    }
    gen_stmt(node->then);
    if (node->inc) gen_expr(node->inc);
    printf("  jmp .L.begin.%d\n.L.end.%d:\n", l, r);
    return;
  case ND_BLOCK:
    for (Node *n = node->body; n; n = n->next) gen_stmt(n);
    return;
  case ND_RETURN:
    gen_expr(node->lhs);
    printf("  jmp .L.return.%s\n", current_fn);
    return;
  case ND_EXPR_STMT:
    gen_expr(node->lhs);
    return;
  default:
    error("invalid statement");
  }
}

static void codegen(Obj *prog) {
  printf("  .text\n");
  for (Obj *fn = prog; fn; fn = fn->next) {
    if (!fn->is_function) continue;
    current_fn = fn->name;
    printf("  .globl %s\n%s:\n  push %%rbp\n  mov %%rsp, %%rbp\n  sub $%d, %%rsp\n",
           fn->name, fn->name, fn->stack_size);
    int i = 0;
    for (Obj *p = fn->params; p; p = p->param_next) {
      printf("  mov %s, %d(%%rbp)\n", argreg[i++], -p->offset);
    }
    gen_stmt(fn->body);
    printf(".L.return.%s:\n  mov %%rbp, %%rsp\n  pop %%rbp\n  ret\n", fn->name);
  }
}

static char *read_file(char *path) {
  FILE *fp = fopen(path, "r");
  if (!fp) { perror(path); exit(1); }
  fseek(fp, 0, SEEK_END);
  long sz = ftell(fp);
  fseek(fp, 0, SEEK_SET);
  char *buf = calloc(1, sz + 2);
  if (fread(buf, 1, sz, fp) != (size_t)sz && ferror(fp)) error("read failed");
  if (sz == 0 || buf[sz - 1] != '\n') buf[sz++] = '\n';
  buf[sz] = 0;
  fclose(fp);
  return buf;
}

int main(int argc, char **argv) {
  if (argc != 2) error("usage: minic <file.c>");
  current_file = argv[1];
  user_input = read_file(current_file);
  Token *tok = tokenize(user_input);
  Obj *prog = parse(tok);
  codegen(prog);
  return 0;
}
