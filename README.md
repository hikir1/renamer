# renamer
## An RE Tool for JavaScript Function Renaming and Commenting

Inspired by AGDCservices
[Preview_Function_Capabilities.py](https://github.com/AGDCservices/Ghidra-Scripts#preview_function_capabilitiespy),
[Gepetto](https://github.com/JusticeRage/Gepetto/tree/main),
and
[AskJOE](https://github.com/securityjoes/AskJOE).

Features:
- Make every function name a unique identifier
- Give every anonymous function a name
- Create a cross reference list for each function
- Rename selected functions using AI suggested names
- Use AI to add comment headers to selected functions
- Use AI to add line comments to selected functions

## Usage

```
renamer.py [OPTION]... [INFILE [OUTFILE [FUNCTION]...]]
```

The input
file INFILE can be a script or a module. With no INFILE or OUTFILE,
or when INFILE or OUTFILE is `-`, read from standard input and write
to standard output, respectively. A list of FUNCTIONs may be provided,
either by name or line number, in which case changes will only affect
those FUNCTIONs in the list. Following `--`, all arguments starting with
a `-` will be treated as normal arguments.

The options below may be used to select the desired behavior. By default,
all arrow functions will be converted to function expressions, and all
function definitions and function expressions will have a unique identifier.

Some of the options, namely `-d`, `-l`, and `-n`, require the organization and API
key of a payed openai account. These can be provided by the `OPENAI_ORG`
and `OPENAI_API_KEY` environment variables, respectively.

| Option                  | Description                                                  |
|-------------------------|--------------------------------------------------------------|
| `-x`, `--list-xrefs`    | include a list of crossreferences before each function       |
| `-d`, `--description`   | include an ai generated header with a description            |
| `-l`, `--line-comments` | include ai generated line comments within each function      |
| `-c`, `--cnt-xrefs`     | include the number of crossreferences in the function's name |
| `-n`, `--ai-name`       | use ai to generate a more intuitive function name            |
| `-h`, `--help`          | show this help message and exit                              |
| `-V`, `--version`       | show version information and exit                            |
