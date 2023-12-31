#!/bin/env python
from typing import NoReturn, Optional, Any, Final, Callable, Union, List, Dict, Set
import enum

import sys
import time
import esprima
from esprima.syntax import Syntax
from esprima import nodes
import escodegen

SLEEP = False

import openai
import os

openai.organization = os.getenv("OPENAI_ORG")
openai.api_key = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = "gpt-4"
OPENAI_MAX_TOKENS = 8192
OPENAI_TEMPERATURE = 0.2

version = """renamer 1.1.1
License GPLv3+: GNU GPL version 3 or later <https://gnu.org/licenses/gpl.html>
This is free software: you are free to change and redistribute it.
There is NO WARRANTY, to the extent permitted by law.

Written by Richard Pawelkiewicz."""

usage = (
    f"Usage: {sys.argv[0]} [OPTION]... [INFILE [OUTFILE [FUNCTION]...]]"
    + """

Rename and add comments to obfuscated JavaScript functions. The input
file INFILE can be a script or a module. With no INFILE or OUTFILE,
or when INFILE or OUTFILE is -, read from standard input and write
to standard output, respectively. A list of FUNCTIONs may be provided,
either by name or line number, in which case changes will only affect
those FUNCTIONs in the list. Following --, all arguments starting with a
- will be treated as normal arguments.

The options below may be used to select the desired behavior. By default,
all arrow functions will be converted to function expressions, and all
function definitions and function expressions will have a unique identifier.

Some of the options, namely -d, -l, and -n, require the organization and API
key of a payed openai account. These can be provided by the OPENAI_ORG
and OPENAI_API_KEY environment variables, respectively.

  -x, --list-xrefs      include a list of crossreferences before each function
  -d, --description     include an ai generated header with a description
  -l, --line-comments   include ai generated line comments within each function
  -c, --cnt-xrefs       include the number of crossreferences in the function's name
  -n, --ai-name         use ai to generate a more intuitive function name
  -h, --help            show this help message and exit
  -V, --version         show version information and exit

Project homepage: <https://github.com/hikir1/renamer>
Report bugs to <https://github.com/hikir1/renamer/issues>"""
)

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
        "safeConcatenation": True,
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


def error(msg: str, *args: Any, **kwargs: Any) -> NoReturn:
    print(f"{sys.argv[0]}: {msg}", *args, file=sys.stderr, **kwargs)
    exit(1)


def warning(msg: str, *args: Any, **kwargs: Any) -> None:
    print(f"{sys.argv[0]}: {msg}", *args, file=sys.stderr, **kwargs)


class IterCmd(enum.Enum):
    STOP: Final = enum.auto()
    CONT: Final = enum.auto()
    SKIP_CHILDREN: Final = enum.auto()

class Scope:
    def __init__(self) -> None:
        self.nodes: List[nodes.Node] = []
        self.ctx: Dict[Any, Any] = {}

    def __repr__(self) -> None:
        return f"Scope(\n  nodes: {self.nodes}\n  ctx: {self.ctx}\n)"

AST = Union[nodes.Module, nodes.Script]
Callback = Callable[[nodes.Node, List[Scope]], IterCmd]

def iter_nodes(ast: AST, prescope: Callback = None, postscope: Callback = None, initctx: Dict[Any, Any] = {}):
    """
    Visit every node of the AST, calling prescope
    function before a new scope is added, if one is added,
    and postscope only after a new scope is added.

    :param ast: The abstract syntax tree, as returned by esprima
    :param prescope: callback function called for every node
    :param postscope: callback function called only after a new scope is added
    :param initctx: The starting value of scopes[0].ctx
    """

    scopes: List[Scope] = [Scope()]
    scopes[0].nodes.append(ast)
    scopes[0].ctx = initctx

    while True:
        # end of scope
        while len(scopes) > 0 and len(scopes[-1].nodes) == 0:
            scopes.pop()
        if len(scopes) == 0:
            break

        node: nodes.Node = scopes[-1].nodes.pop()

        if prescope:
            ret: IterCmd = prescope(node, scopes)
            if ret == IterCmd.STOP:
                break
            elif ret == IterCmd.SKIP_CHILDREN:
                continue

        # beginning of new scope
        if hasattr(node, "body") and node.body:
            scopes.append(Scope())
            if postscope:
                ret: IterCmd = postscope(node, scopes)
                if ret == IterCmd.STOP:
                    break
                elif ret == IterCmd.SKIP_CHILDREN:
                    continue

        # find all subnodes
        for attr in dir(node):
            attr: Any = getattr(node, attr)
            if isinstance(attr, list):
                if len(attr) > 0 and isinstance(attr[0], nodes.Node):
                    scopes[-1].nodes += attr[::-1]
            elif isinstance(attr, nodes.Node):
                scopes[-1].nodes.append(attr)


def process_comments(ast: AST) -> None:
    """
    After parsing the code with esprima, take the list of comments
    and attach them to the correct node as leading or trailing comments,
    as understood by escodegen.

    :param ast: The abstract syntax tree, as returned by esprima
    """
    comment: Union[nodes.LineComment, nodes.BlockComment]
    for comment in ast.comments:

        def prescope(node: nodes.Node, scopes: List[Scope]) -> IterCmd:
            # Skip the first node, bc for some reason it starts
            # at the first line of code, after any comments.
            # We want to add any preceding comments to the first node
            # inside the program.
            if node.type == Syntax.Program or node.type == "Line":
                return IterCmd.CONT

            # If this is the first node beyond the comment,
            # add it as a leading comment
            if comment.range[0] < node.range[0]:
                if not node.leadingComments:
                    node.leadingComments = []
                node.leadingComments.append(comment)
                return IterCmd.STOP

            # If the comment appears within this node and it has
            # no child nodes, or if it appears after this node
            # and there are no more nodes after this one in the
            # current scope, add it as a trailing comment
            elif (
                comment.range[0] > node.range[0]
                and comment.range[0] < node.range[1]
                and (not hasattr(node, "body") or node.body == [])
            ) or (
                comment.range[0] > node.range[1]
                and (
                    len(scopes[-1].nodes) == 0
                    or comment.loc.start.line == node.loc.end.line
                )
            ):
                if not node.trailingComments:
                    node.trailingComments = []
                node.trailingComments.append(comment)
                return IterCmd.STOP

            # If the comment appears after this node, we will
            # probably go on to the next one
            return IterCmd.CONT

        def postscope(node: nodes.Node, scopes: List[Scope]) -> IterCmd:
            if node.type == Syntax.Program:
                return IterCmd.CONT
            # if the comment appears completely after this scope,
            # skip it
            if comment.loc.start.line > node.loc.end.line:
                scopes.pop()
            return IterCmd.CONT

        iter_nodes(ast, prescope, postscope)


def get_allnames(ast: AST) -> Set[str]:
    """
    Retrieve a set of all indentifiers used in the program

    :param ast: The abstract syntax tree, as returned by esprima
    :return: set of identifier names
    """

    allnames: Set[str] = set()

    def prescope(node: nodes.Node, scopes: List[Scope]) -> IterCmd:
        if node.type == Syntax.Identifier:
            allnames.add(node.name)
        return IterCmd.CONT

    iter_nodes(ast, prescope)

    return allnames


class Xref:
    def __init__(self, caller: "Function", lineno: str):
        self.caller: "Function" = caller
        self.lineno: str = lineno


class Function:
    def __init__(self, name: str):
        self.name: str = name
        self.xrefs: List[Xref] = []
        self.isCreatorUnkown: bool = False


def get_funcs(ast: AST) -> Dict[str, Function]:
    """
    Gather information on all functions.

    :param ast: The abstract syntax tree, as returned by esprima
    :return: A dictionary mapping function names to function objects
    """

    funcs: Dict[str, Function] = {}

    # When a function is called, include the current function in
    # its cross reference list. Ignore call expressions when the
    # callee is anything but a simple identifier.
    def prescope(node: nodes.Node, scopes: List[Scope]) -> IterCmd:
        if node.type == Syntax.CallExpression:
            if node.callee.type == Syntax.Identifier:
                if not node.callee.name in funcs:
                    funcs[node.callee.name] = Function(node.callee.name)
                    funcs[node.callee.name].isCreatorUnknown = True
                funcs[node.callee.name].xrefs.append(
                    Xref(scopes[-1].ctx["func"], node.loc.start.line)
                )
            else:
                # TODO handle this case?
                pass
        return IterCmd.CONT

    # When a new function is entered, add it to the scope. Otherwise,
    # use the function from the previous scope.
    def postscope(node: nodes.Node, scopes: List[Scope]) -> IterCmd:
        if (
            node.type == Syntax.FunctionDeclaration
            or node.type == Syntax.FunctionExpression
        ):
            name = "! Anonymous" if not node.id else node.id.name
            scopes[-1].ctx["func"] = Function(name)
            funcs[name] = scopes[-1].ctx["func"]
        else:
            scopes[-1].ctx["func"] = scopes[-2].ctx["func"]
        return IterCmd.CONT

    iter_nodes(ast, prescope, postscope, initctx={"func": Function("! Global Scope")})

    return funcs


def uniquify(ast, allnames, includefuncs):
    """
    Ensure all function identifiers are unique.

    :param nodes.Program ast: The abstract syntax tree, as returned by esprima
    :param set(str) allnames: Set of all existing names, as returned by get_allnames()
    :param set(str) includefuncs: Names and/or line numbers of functions to consider.
      If blank, all named functions are considered.
    """

    # TODO sub names after functions have been processed

    # ensure name is unique
    def subname(node, subs):
        prefix = "f_"
        name = f"{prefix}{node.id.name}"
        num = 1
        # add random digits to the end of the identifier until it is unique
        while name in allnames:
            num += 1
            name = f"{prefix}{node.id.name}{num}"
        # subtitute all uses of this identifier in the current scope, at least until shadowed
        subs[node.id.name] = name
        allnames.add(name)
        if len(includefuncs) > 0:
            includefuncs.add(name)
        node.id.name = name

    def prescope(node, scopes):
        # make sure to process right side of assignment before
        # resetting id association, if applicable
        if (
            node.type == Syntax.AssignmentExpression
            or node.type == Syntax.AssignmentPattern
        ):
            if node.left.type == Syntax.Identifier:
                for scope in scopes[::-1]:
                    if node.left.name in scope.ctx["subs"]:
                        # record left as resetting id association
                        scopes[-1].ctx["lefts"].add(node.id)
                        break
            # make sure right is processed before left
            scopes[-1].nodes += [node.left, node.right]
            return IterCmd.SKIP_CHILDREN

        # make sure to process initializer before
        # resetting id association, if applicable
        elif node.type == Syntax.VariableDeclarator:
            for scope in scopes[::-1]:
                if node.id.name in scope.ctx["subs"]:
                    # record left as resetting id association
                    scopes[-1].ctx["lefts"].add(node.id)
                    break
            # make sure right is processed before left (if it exists)
            scopes[-1].nodes.append(node.id)
            if hasattr(node, "init") and node.init:
                scopes[-1].nodes.append(node.init)
            return IterCmd.SKIP_CHILDREN

        # only consider the object of a member expression.
        # ignore the property.
        elif node.type == Syntax.MemberExpression:
            scopes[-1].nodes.append(node.object)
            return IterCmd.SKIP_CHILDREN

        # uniquify the function name of a function declaration
        elif node.type == Syntax.FunctionDeclaration:
            if (
                len(includefuncs) == 0
                or node.id.name in includefuncs
                or str(node.loc.start.line) in includefuncs
            ):
                subname(node, scopes[-1].ctx["subs"])

        elif node.type == Syntax.Identifier:
            # if id was reset, ignore
            if node in scopes[-1].ctx["lefts"]:
                scopes[-1].ctx["subs"][node.name] = None
                scopes[-1].ctx["lefts"].remove(node)
            # otherwise, substitute
            else:
                for scope in scopes[::-1]:
                    newname = scope.ctx["subs"].get(node.name, None)
                    if newname:
                        node.name = newname
                        break
        return IterCmd.CONT

    def postscope(node, scopes):
        scopes[-1].ctx["subs"] = {}
        scopes[-1].ctx["lefts"] = set()

        # if its a function expression, its name only matters within its own scope
        # so we create a substitution after making the new scope
        if node.type == Syntax.FunctionExpression:
            if hasattr(node, "id") and node.id:
                if (
                    len(includefuncs) == 0
                    or node.id.name in includefuncs
                    or str(node.loc.start.line) in includefuncs
                ):
                    subname(node, scopes[-1].ctx["subs"])

        # check for overlapping parameter names
        if (
            node.type == Syntax.FunctionDeclaration
            or node.type == Syntax.FunctionExpression
            or node.type == Syntax.ArrowFunctionExpression
            or node.type == Syntax.ArrowParameterPlaceHolder
        ):
            for param in node.params:
                if param.type == Syntax.Identifier:
                    name = param.name
                else:
                    assert param.type == Syntax.AssignmentExpression
                    assert param.left.type == Syntax.Identifier
                    name = param.left.name
                for scope in scopes:
                    if name in scope.ctx["subs"]:
                        scopes[-1].ctx["subs"][name] = None
                        break

        return IterCmd.CONT

    iter_nodes(ast, prescope, postscope, initctx={"subs": {}, "lefts": set()})


new_id_cnt = 0


def normalize(ast, allnames, includefuncs):
    nodestack = [ast]

    def new_id():
        while True:
            global new_id_cnt
            name = f"f_e_{new_id_cnt}"
            new_id_cnt += 1
            if not name in allnames:
                break
        allnames.add(name)
        if len(includefuncs) > 0:
            includefuncs.add(name)
        return nodes.Identifier(name)

    while len(nodestack) > 0:
        node = nodestack.pop()

        for sattr in dir(node):
            attr = getattr(node, sattr)
            if isinstance(attr, nodes.Node):
                if attr.type == Syntax.FunctionExpression:
                    if not hasattr(attr, "id") or not attr.id:
                        if (
                            len(includefuncs) == 0
                            or str(attr.loc.start.line) in includefuncs
                        ):
                            attr.id = new_id()
                elif attr.type == Syntax.ArrowFunctionExpression:
                    if (
                        len(includefuncs) == 0
                        or str(attr.loc.start.line) in includefuncs
                    ):
                        newattr = Syntax.FunctionExpression(
                            new_id(), dec.init.params, dec.init.body, False
                        )
                        setattr(node, sattr, newattr)
                nodestack.append(attr)
            elif (
                isinstance(attr, list)
                and len(attr) > 0
                and isinstance(attr[0], nodes.Node)
            ):
                for i in range(len(attr)):
                    if attr[i].type == Syntax.FunctionExpression:
                        if not hasattr(attr[i], "id") or not attr[i].id:
                            if (
                                len(includefuncs) == 0
                                or str(attr[i].loc.start.line) in includefuncs
                            ):
                                attr[i].id = new_id()
                    elif attr[i].type == Syntax.ArrowFunctionExpression:
                        if (
                            len(includefuncs) == 0
                            or str(attr[i].loc.start.line) in includefuncs
                        ):
                            attr[i] = Syntax.FunctionExpression(
                                new_id(), dec.init.params, dec.init.body, False
                            )
                    nodestack.append(attr[i])


def ai_add_comments(func, dodesc, doline):
    if not dodesc and not doline:
        return func
    code = escodegen.generate(func, escodegen_config)
    max_tokens = int(len(code) * 2.6)
    if max_tokens > OPENAI_MAX_TOKENS:
        return
    message = f"Can you please add comments to the following JavaScript function?"
    if doline:
        message += "Include a few line comments. Don't comment every line, \
                and please ignore any nested functions."
    else:
        message += "Do not include line comments."
    if dodesc:
        message += "Include a header with a general description of the function, arguments, \
                    and return value."
    else:
        message += "Do not include a block comment header"
    message += f"\n{code}\n"

    res = openai.ChatCompletion.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "user",
                "content": message,
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
            error(
                f"failed to add comments to {func.id.name}. bad response from ai:\n{code}"
            )
        code = code[start2 + 1 : end]
    newast = esprima.parseScript(code, esprima_config)
    process_comments(newast)
    if SLEEP:
        time.sleep(20)
    return newast.body[0]


def ai_suggest_name(func, oldname):
    code = escodegen.generate(func, escodegen_config)
    marker = ">> "
    max_tokens = int(len(code) * 1.4 + 20)
    if max_tokens > OPENAI_MAX_TOKENS:
        warning("function is too big for ai to suggest name")
        return oldname
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
        error(f"failed to get suggested function name. bad response from ai:\n{name}")
    name = name[start + len(marker) :]
    name = name.lstrip().split()[0]
    name.strip("`'\"()")
    if SLEEP:
        time.sleep(20)
    return name


def add_comments(ast, funcs, includefuncs, doxrefs, dodesc, doline):
    nodestack = [ast]

    while len(nodestack) > 0:
        node = nodestack.pop()
        name = "! Anonymous" if not node.id else node.id.name

        if (
            node.type == Syntax.FunctionDeclaration
            or node.type == Syntax.FunctionExpression
        ):
            if (
                len(includefuncs) == 0
                or name in includefuncs
                or str(node.loc.start.line) in includefuncs
            ):
                newnode = ai_add_comments(node, dodesc, doline)
                node.params = newnode.params
                node.body = newnode.body
                if hasattr(newnode, "leadingComments"):
                    node.leadingComments = newnode.leadingComments
                if hasattr(newnode, "trailingComments"):
                    node.trailingComments = newnode.trailingComments

                if doxrefs:
                    func = funcs[name]
                    if len(func.xrefs) > 0:
                        if (
                            not hasattr(node, "leadingComments")
                            or not node.leadingComments
                        ):
                            node.leadingComments = []
                        callers_cnt = {}
                        for xref in func.xrefs:
                            callers_cnt[xref.caller.name] = (
                                callers_cnt.get(xref.caller.name, 0) + 1
                            )
                        comment = "*\n * xrefs {{{\n"
                        comment += "".join(
                            map(lambda x: f" *   {x}: {callers_cnt[x]}\n", callers_cnt)
                        )
                        comment += " * }}}\n "
                        node.leadingComments.append(nodes.BlockComment(comment))

        for sattr in dir(node):
            attr = getattr(node, sattr)
            if isinstance(attr, nodes.Node):
                nodestack.append(attr)
            elif (
                isinstance(attr, list)
                and len(attr) > 0
                and isinstance(attr[0], nodes.Node)
            ):
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

    def __init__(self, allnames, funcs, includefuncs, docntxrefs, doainame):
        self.allnames = allnames
        self.funcs = funcs
        self.includefuncs = includefuncs
        self.docntxrefs = docntxrefs
        self.doainame = doainame

    def visit(self, ast, *args, **kwargs):
        self.subs = {}
        super().visit(ast, *args, **kwargs)
        FunctionRenamer.PostProcessor(self.subs).visit(ast)

    def handle_function(self, node):
        # skip functions that already start with "F_".
        # this allows the script to recognize manually named functions
        oldname = "! Anonymous" if not node.id else node.id.name
        if oldname.startswith("F_"):
            return
        if len(self.includefuncs) > 0:
            if (
                not oldname in self.includefuncs
                and not str(node.loc.start.line) in self.includefuncs
            ):
                return

        func = self.funcs.pop(oldname)
        prefix = "f_e_" if oldname.startswith("f_e_") else "f_"
        if self.doainame:
            name = prefix + ai_suggest_name(node, oldname)
        else:
            name = prefix + oldname
        if self.docntxrefs:
            name += f"_xref_{len(func.xrefs)}"
        final_name = name
        cnt = 1
        while final_name in self.allnames:
            cnt += 1
            final_name = f"{name}_{cnt}"
        self.allnames.add(final_name)
        func.name = final_name
        self.funcs[final_name] = func
        self.subs[oldname] = final_name
        node.id.name = final_name
        if len(self.includefuncs) > 0:
            self.includefuncs.add(final_name)

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


def main():
    endflags = False
    infile = None
    outfile = None
    includefuncs = set()
    argidx = 0
    doxrefs = False
    dodesc = False
    doline = False
    docntxrefs = False
    doainame = False

    # parse args
    for arg in sys.argv[1:]:
        if not endflags and arg.startswith("-"):
            if arg == "-":
                if argidx == 2:
                    error("unexpected argument '-'")
                argidx += 1
            elif arg == "--":
                endflags = True
            elif arg == "-x" or arg == "--list-xrefs":
                doxrefs = not doxrefs
            elif arg == "-d" or arg == "--description":
                dodesc = not dodesc
            elif arg == "-l" or arg == "--line-comments":
                doline = not doline
            elif arg == "-c" or arg == "--cnt-xrefs":
                docntxrefs = not docntxrefs
            elif arg == "-n" or arg == "--ai-name":
                doainame = not doainame
            elif arg == "-h" or arg == "--help":
                print(usage)
                return
            elif arg == "-V" or arg == "--version":
                print(version)
                return
            else:
                error(
                    f"invalid option '{arg}'\nTry {sys.argv[0]} --help for more information."
                )
        else:
            if argidx == 0:
                infile = arg
            elif argidx == 1:
                outfile = arg
            else:
                includefuncs.add(arg)
            argidx += 1

    if (dodesc or doline or doainame) and (
        not openai.organization or not openai.api_key
    ):
        error(
            f"--description, --line-comments, and --ai-name require \
                an openai organization and API key to work.\n \
                Try {sys.argv[0]} --help for more information."
        )

    if infile:
        try:
            with open(infile) as f:
                code = f.read()
        except OSError as e:
            error(f"failed to read input file '{infile}': {e.strerror}")
    else:
        code = sys.stdin.read()

    if outfile:
        try:
            out = open(outfile, "w")
        except OSError as e:
            error(f"failed to open output file '{outfile}': {e.strerror}")
    else:
        out = sys.stdout

    parse = esprima.parseScript
    if "import" in code or "export" in code:
        parse = esprima.parseModule
    ast = parse(code, esprima_config)

    process_comments(ast)
    allnames = get_allnames(ast)

    uniquify(ast, allnames, includefuncs)
    normalize(ast, allnames, includefuncs)

    funcs = get_funcs(ast)
    FunctionRenamer(allnames, funcs, includefuncs, docntxrefs, doainame).visit(ast)
    add_comments(ast, funcs, includefuncs, doxrefs, dodesc, doline)

    code = escodegen.generate(ast, escodegen_config)
    out.write(code)
    if outfile:
        out.close()


# TODO preserve comments that already exist

if __name__ == "__main__":
    main()
