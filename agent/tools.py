from langchain.tools import tool
from langchain_community.tools import DuckDuckGoSearchRun

_search = DuckDuckGoSearchRun()


@tool
def search_web(query: str) -> str:
    """Searches the internet for current information about any
    topic. Use this when the user asks about recent events, news,
    or facts that require looking up rather than answering from
    memory."""
    try:
        result = _search.run(query)
        if not result or len(result) < 10:
            return "Search returned no results. Try rephrasing your query."
        return result
    except Exception as e:
        return f"Search failed: {str(e)}. Try a different query."


@tool
def get_word_length(word: str) -> int:
    """Returns the number of characters in a word or phrase.
    Use this when asked to count letters or characters."""
    return len(word.strip())


@tool
def calculate(expression: str) -> str:
    """Evaluates a simple math expression like '9 + 10' and
    returns the result. Numbers and operators only, no words."""
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return str(result)
    except Exception as e:
        return f"Could not calculate '{expression}': {str(e)}"
