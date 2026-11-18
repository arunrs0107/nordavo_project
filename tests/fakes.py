class FakeResp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body if body is not None else {}, headers or {}
        self.content = b"x"
        self.text = str(self._body)

    def json(self):
        return self._body


class FakeSession:
    """Plays back a script of responses (or exceptions) and records calls."""
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def request(self, method, url, **kw):
        self.calls.append((method, url))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


class FakeClaude:
    """Returns scripted tool outputs in order; records how many calls were made."""
    def __init__(self, outputs):
        from agent.llm import Usage
        self.outputs, self.calls, self.usage = list(outputs), 0, Usage()

    def call_tool(self, system, user, tool, max_tokens=1500):
        self.calls += 1
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out
