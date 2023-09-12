#!/bin/env python

import sys
import esprima
from esprima.syntax import Syntax
from esprima import nodes
import escodegen

esprima_config = {
    "jsx": False,
    "range": True,
    "loc": True,
    "tolerant": True,
    "tokens": False,
    "comment": True,
    }

escodegen_config = {
    "format": {
        "indent": {
            "style": "\t",
            "base": 0,
            "adjustMultilineComment": False,
            },
        "newline": "\n",
        "space": " ",
        "json": False,
        "renumber": False,
        "hexadecimal": False,
        "quotes": "double",
        "escapeless": False,
        "compact": False,
        "parenthesis": True,
        "semicolons": True,
        "safeConcatenation": True
        },
    "moz": {
        "starlessGenerator": False,
        "parenthesizedComprehensionBlock": False,
        "comprehensionExpressionStartsWithAssignment": False,
        },
    "parse": esprima.parse,
    "comment": True,
    "sourceMap": None,
    "sourceMapRoot": None,
    "sourceMapWithCode": False,
    "file": None,
    "directive": False,
    "verbatim": None,
    }

if len(sys.argv) < 3:
    print(f"Usage: {sys.argv[0]} input output")
    exit(1)

with open(sys.argv[1]) as f:
    code = f.read()

parse = esprima.parseScript
if "import" in code or "export" in code:
    parse = esprima.parseModule

ast = parse(code, esprima_config)

def process_comments(ast):
    for comment in ast.comments:
        line = comment.loc.start
        nodes = list(ast.body)
        while True:
            node = nodes.pop(0)
            if comment.range[0] < node.range[0]:
                if not node.leadingComments:
                    node.leadingComments = []
                node.leadingComments.append(comment)
                break
            elif (comment.range[0] > node.range[0] \
                    and comment.range[0] < node.range[1] \
                    and (not hasattr(node, "body") or node.body == [])) \
                    or (comment.range[0] > node.range[1] and
                            (len(nodes) == 0 or comment.loc.start.line == node.loc.end.line)):
                if not node.trailingComments:
                    node.trailingComments = []
                node.trailingComments.append(comment)
                break
            elif comment.range[0] > node.range[0]:
                if comment.range[0] < node.range[1]:
                    if isinstance(node.body, list):
                        nodes = list(node.body)
                    else:
                        nodes = [node.body]

process_comments(ast)

allnames = set()
class IdVisitor(esprima.NodeVisitor):
    def visit_Identifier(self, id):
        allnames.add(id.name)
        return self.visit_Object(id)
    def visit_LabeledStatement(self, statement):
        allnames.add(statement.label)
        return self.visit_Object(statement)

IdVisitor().visit(ast)

class Xref:
    def __init__(self, caller, lineno):
        self.caller = caller
        self.lineno = lineno

class Function:
    def __init__(self, name):
        self.name = name
        self.xrefs = []
        self.isCreatorUknown = False

def get_funcs():
    class Scope:
        def __init__(self, funcname):
            self.nodes = []
            self.func = Function(funcname)

    scopes = [Scope("! Global Scope")]
    scopes[0].nodes.append(ast)

    funcs = {}

    while True:
        # end of scope
        while len(scopes) > 0 and len(scopes[-1].nodes) == 0:
            scopes.pop()
        if len(scopes) == 0:
            break

        node = scopes[-1].nodes.pop()

        if node.type == Syntax.FunctionDeclaration \
                or node.type == Syntax.FunctionExpression:
            scopes.append(Scope(node.id.name))
            funcs[node.id.name] = scopes[-1].func

        elif node.type == Syntax.CallExpression:
            if node.callee.type == Syntax.Identifier:
                if not node.callee.name in funcs:
                    funcs[node.callee.name] = Function(node.callee.name)
                    funcs[node.callee.name].isCreatorUknown = True
                funcs[node.callee.name].xrefs.append(Xref(scopes[-1].func, node.loc.start.line))
            else:
                # TODO handle this case?
                pass

        for attr in dir(node):
            attr = getattr(node, attr)
            if isinstance(attr, list):
                if len(attr) > 0 and isinstance(attr[0], nodes.Node):
                    scopes[-1].nodes += attr[::-1]
            elif isinstance(attr, nodes.Node):
                scopes[-1].nodes.append(attr)
    return funcs


def uniquify():
    class Scope:
        def __init__(self, nodes):
            self.nodes = nodes
            self.subs = {}
            self.lefts = set()

        def __repr__(self):
            return f"Scope(\n  {self.nodes}\n  {self.subs}\n)"

    scopes = [Scope([ast])]

    while True:
        # end of scope
        while len(scopes) > 0 and len(scopes[-1].nodes) == 0:
            scopes.pop()
        if len(scopes) == 0:
            break

        node = scopes[-1].nodes.pop()

        def subName(node):
            prefix = "f_"
            name = f"{prefix}{node.id.name}"
            num = 1
            # add random digits to the end of the identifier until it is unique
            while name in allnames:
                num += 1
                name = f"{prefix}{node.id.name}{num}"
            # subtitute all uses of this identifier in the current scope, at least until shadowed
            scopes[-1].subs[node.id.name] = name
            allnames.add(name)
            node.id.name = name

        if node.type == Syntax.AssignmentExpression \
                or node.type == Syntax.AssignmentPattern:
            if node.left.type == Syntax.Identifier:
                for scope in scopes[::-1]:
                    if node.left.name in scope.subs:
                        # record left as resetting id association
                        scopes[-1].lefts.add(node.id)
                        break
            # make sure right is processed before left
            scopes[-1].nodes += [node.left, node.right]
            continue

        elif node.type == Syntax.VariableDeclarator:
            for scope in scopes[::-1]:
                if node.id.name in scope.subs:
                    # record left as resetting id association
                    scopes[-1].lefts.add(node.id)
                    break
            # make sure right is processed before left (if it exists)
            scopes[-1].nodes.append(node.id)
            if hasattr(node, "init") and node.init:
                scopes[-1].nodes.append(node.init)
            continue

        elif node.type == Syntax.MemberExpression:
            # only consider the object. ignore the property.
            scopes[-1].nodes.append(node.object)
            continue

        elif node.type == Syntax.FunctionDeclaration:
            subName(node)

        elif node.type == Syntax.Identifier:
            # if id was reset, ignore
            if node in scopes[-1].lefts:
                scopes[-1].subs[node.name] = None
                scopes[-1].lefts.remove(node)
            else:
                for scope in scopes[::-1]:
                    if node.name in scope.subs:
                        newname = scope.subs[node.name]
                        if newname:
                            node.name = newname

        if hasattr(node, "body") and node.body:
            scopes.append(Scope([]))

        # if its a function expression, its name only matters within its own scope
        # so we create a substitution after making the new scope
        elif node.type == Syntax.FunctionExpression:
            if hasattr(node, "id") and node.id:
                subName(node)

        if node.type == Syntax.FunctionDeclaration \
                or node.type == Syntax.FunctionExpression \
                or node.type == Syntax.ArrowFunctionExpression \
                or node.type == Syntax.ArrowParameterPlaceHolder:
            for param in node.params:
                if param.type == Syntax.Identifier:
                    name = param.name
                else:
                    assert(param.type == Syntax.AssignmentExpression)
                    assert(param.left.type == Syntax.Identifier)
                    name = param.left.name
                for scope in scopes:
                    if name in scope.subs:
                        scopes[-1].subs[name] = None
                        break

        for attr in dir(node):
            attr = getattr(node, attr)
            if isinstance(attr, list):
                if len(attr) > 0 and isinstance(attr[0], nodes.Node):
                    scopes[-1].nodes += attr[::-1]
            elif isinstance(attr, nodes.Node):
                scopes[-1].nodes.append(attr)

newIdCnt = 0
def normalize():
    nodestack = [ast]

    def newId():
        while True:
            global newIdCnt
            name = f"f_e_{newIdCnt}"
            newIdCnt += 1
            if not name in allnames:
                break
        allnames.add(name)
        return nodes.Identifier(name)

    while len(nodestack) > 0:
        node = nodestack.pop()

        for sattr in dir(node):
            attr = getattr(node, sattr)
            if isinstance(attr, nodes.Node):
                if attr.type == Syntax.FunctionExpression:
                    if not hasattr(attr, "id") or not attr.id:
                        attr.id = newId()
                elif attr.type == Syntax.ArrowFunctionExpression:
                    newattr = Syntax.FunctionExpression(newId(), dec.init.params, dec.init.body, False)
                    setattr(node, sattr, newattr)
                nodestack.append(attr)
            elif isinstance(attr, list) and len(attr) > 0 and isinstance(attr[0], nodes.Node):
                for i in range(len(attr)):
                    if attr[i].type == Syntax.FunctionExpression:
                        if not hasattr(attr[i], "id") or not attr[i].id:
                            attr[i].id = newId()
                    elif attr[i].type == Syntax.ArrowFunctionExpression:
                        attr[i] = Syntax.FunctionExpression(newId(), dec.init.params, dec.init.body, False)
                    nodestack.append(attr[i])

import openai
import os
openai.organization = "org-XfD8N76UJAj6sSTJEGHqa3eg"
openai.api_key = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = "gpt-4"
OPENAI_MAX_TOKENS = 8192
OPENAI_TEMPERATURE = 0.2


def ai_add_comments(func):
    code = escodegen.generate(func, escodegen_config)
    max_tokens = int(len(code) * 2.6)
    if max_tokens > OPENAI_MAX_TOKENS:
        return
    print(f"requesting comments for {func.id.name}...")
    res = openai.ChatCompletion.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": f"Can you please add comments to the following JavaScript function? \
                            Include a few line comments and a header with a general description of the function, arguments, \
                            and return value. Don't comment every line, and please ignore any nested functions.\n{code}\n",
                },
            ],
            max_tokens=max_tokens,
            temperature=OPENAI_TEMPERATURE,
            )
    code = res["choices"][0]["message"]["content"]
    start = code.find("```")
    start2 = code.find("\n", start)
    end = code.find("```", start2)
    if start != -1:
        if start2 != -1 or end == -1 or start2 > end:
            print(f"Failed to add comments to {func.id.name}")
            return
        code = code[start2 + 1:end]
    func = esprima.parseScript(code, esprima_config)
    process_comments(func)
    return func

def ai_suggest_name(func):
    code = escodegen.generate(func, escodegen_config)
    marker = ">> "
    max_tokens = int(len(code) * 1.4 + 20)
    if max_tokens > OPENAI_MAX_TOKENS:
        print("Warning: Function is too big for ai to suggest name")
        return func.id.name
    res = openai.ChatCompletion.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": f"Can you please suggest a better name for the following JavaScript function? \
                        Please precede the suggested name with '{marker}'.\n{code}\n",
                },
            ],
            max_tokens=max_tokens,
            temperature=OPENAI_TEMPERATURE,
            )
    name = res["choices"][0]["message"]["content"]
    start = name.rfind(marker)
    if start == -1:
        print(f"Failed to get suggested function name")
        return
    name = name[start + len(marker):]
    name = name.lstrip().split()[0]
    name.strip("`'\"()")
    return name

uniquify()
normalize()

def add_comments(ast, funcs, do_xrefs):
    nodestack = [ast]

    while len(nodestack) > 0:
        node = nodestack.pop()

        if node.type == Syntax.FunctionDeclaration \
                or node.type == Syntax.FunctionExpression:
            newast = ai_add_comments(node)
            newnode = newast.body[0]
            node.params = newnode.params
            node.body = newnode.body
            if hasattr(newnode, "leadingComments"):
                node.leadingComments = newnode.leadingComments
            if hasattr(newnode, "trailingComments"):
                node.trailingComments = newnode.trailingComments

            if do_xrefs:
                func = funcs[node.id.name]
                if len(func.xrefs) > 0:
                    if not hasattr(node, "leadingComments") \
                            or not node.leadingComments:
                        node.leadingComments = []
                    callers_cnt = {}
                    for xref in func.xrefs:
                        callers_cnt[xref.caller.name] = callers_cnt.get(xref.caller.name, 0) + 1
                    comment = "*\n * xrefs {{{\n"
                    comment += "".join(map(lambda x: f" *   {x}: {callers_cnt[x]}\n", callers_cnt))
                    comment += " * }}}\n "
                    node.leadingComments.append(nodes.BlockComment(comment))

        for sattr in dir(node):
            attr = getattr(node, sattr)
            if isinstance(attr, nodes.Node):
                nodestack.append(attr)
            elif isinstance(attr, list) and len(attr) > 0 and isinstance(attr[0], nodes.Node):
                nodestack += attr

class FunctionRenamer(esprima.NodeVisitor):

    class PostProcessor(esprima.NodeVisitor):
        def __init__(self, subs, *args, **kwargs):
            self.subs = subs
            super().__init__(*args, **kwargs)

        def visit_Identifier(self, node):
            if node.name in self.subs:
                node.name = self.subs[node.name]
            return super().visit_Object(node)

    def __init__(self, funcs):
        self.funcs = funcs

    def visit(self, ast, *args, **kwargs):
        self.subs = {}
        super().visit(ast, *args, **kwargs)
        FunctionRenamer.PostProcessor(self.subs).visit(ast)

    def handle_function(self, node):
        # skip functions that already start with "F_".
        # this allows the script to recognize manually named functions
        if node.id.name.startswith("F_"):
            return

        func = self.funcs.pop(node.id.name)
        prefix = "f_e_" if node.id.name.startswith("f_e_") else "f_"
        name = prefix + ai_suggest_name(node)
        name += f"_xref_{len(func.xrefs)}"
        final_name = name
        cnt = 1
        while final_name in allnames:
            cnt += 1
            final_name = f"{name}_{cnt}"
        allnames.add(final_name)
        func.name = final_name
        self.funcs[final_name] = func
        self.subs[node.id.name] = final_name
        node.id.name = final_name

    def visit_FunctionDeclaration(self, node):
        self.handle_function(node)
        return super().visit_Object(node)

    def visit_AsyncFunctionDeclaration(self, node):
        self.handle_function(node)
        return super().visit_Object(node)

    def visit_FunctionExpression(self, node):
        self.handle_function(node)
        return super().visit_Object(node)

    def visit_AsyncFunctionExpression(self, node):
        self.handle_function(node)
        return super().visit_Object(node)

funcs = get_funcs()
FunctionRenamer(funcs).visit(ast)
add_comments(ast, funcs, do_xrefs=True)

code = escodegen.generate(ast, escodegen_config)
with open(sys.argv[2], "w") as f:
    f.write(code)

print("All done!")
