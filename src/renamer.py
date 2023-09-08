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

print("names before:", allnames)

class Scope:
    def __init__(self, nodes):
        self.nodes = nodes
        self.subs = {}

def uniquify():
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
                        scopes[-1].subs[node.left.name] = None
                        break

        elif node.type == Syntax.VariableDeclarator:
            for scope in scopes[::-1]:
                if node.id.name in scope.subs:
                    scopes[-1].subs[node.id.name] = None
                    break

        elif node.type == Syntax.FunctionDeclaration:
            subName(node)

        elif node.type == Syntax.Identifier:
            for scope in scopes[::-1]:
                if node.name in scope.subs:
                    newname = scope.subs[node.name]
                    if newname:
                        node.name = newname

        if hasattr(node, "body"):
            scopes.append(Scope([]))

        if node.type == Syntax.FunctionExpression:
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
            name = f"fe_{newIdCnt}"
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
                            Include a header with a general description of the function, arguments, \
                            and return value. Don't comment every line.\n{code}\n",
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
    marker = "suggested name:"
    max_tokens = len(code) * 1.4 + 20
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
    start = code.rfind(marker)
    if start == -1:
        print(f"Failed to get suggested function name")
        return
    name = name[start + len(marker):]
    name = name.lstrip().split()[0]
    name.strip("`'\"()")
    return name

uniquify()
normalize()

print("names after unqiue and normalize:", allnames)

class FunctionCommentor(esprima.NodeVisitor):

    @staticmethod
    def handle_function(func):
        newast = ai_add_comments(func)
        newfunc = newast.body[0]
        func.params = newfunc.params
        func.body = newfunc.body
        if hasattr(newfunc, "leadingComments"):
            func.leadingComments = newfunc.leadingComments
        if hasattr(newfunc, "trailingComments"):
            func.trailingComments = newfunc.trailingComments

    def visit_FunctionDeclaration(self, node):
        FunctionCommentor.handle_function(node)
        return super().visit_Object(node)

    def visit_AsyncFunctionDeclaration(self, node):
        FunctionCommentor.handle_function(node)
        return super().visit_Object(node)

    def visit_FunctionExpression(self, node):
        FunctionCommentor.handle_function(node)
        return super().visit_Object(node)

    def visit_AsyncFunctionExpression(self, node):
        FunctionCommentor.handle_function(node)
        return super().visit_Object(node)

FunctionCommentor().visit(ast)

code = escodegen.generate(ast, escodegen_config)
with open(sys.argv[2], "w") as f:
    f.write(code)

print("All done!")
