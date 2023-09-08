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

for comment in ast.comments:
    line = comment.loc.start
    if len(ast.body) == 0:
        break
    nodes = list(ast.body)
    node = ast.body[0]
    while True:
        node = nodes.pop(0)
        if comment.loc.start.line < node.loc.start.line:
            if not node.leadingComments:
                node.leadingComments = []
            node.leadingComments.append(comment)
            break
        elif not node.body or node.body == [] or len(nodes) == 0 \
                or comment.loc.start.line == node.loc.start.line:
            if not node.trailingComments:
                node.trailingComments = []
            node.trailingComments.append(comment)
            break
        elif comment.loc.start.line >= node.loc.start.line:
            if comment.loc.end.line <= node.loc.end.line:
                if node.body is list:
                    nodes = list(node.body)
                else:
                    nodes = [node.body]

allnames = set()
class IdVisitor(esprima.NodeVisitor):
    def visit_Identifier(self, id):
        allnames.add(id.name)
        return self.visit_Generic(id)
    def visit_LabeledStatement(self, statement):
        allnames.add(statement.label)
        return self.visit_Generic(statement)

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
            if hasattr(node, "id"):
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

uniquify()

print("names after:", allnames)

code = escodegen.generate(ast, escodegen_config)
with open(sys.argv[2], "w") as f:
    f.write(code)

print("All done!")
