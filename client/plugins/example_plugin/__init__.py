"""HashMM manifest-first plugin example."""


def register_tools():
    return [
        {
            "name": "hello_plugin",
            "executor": lambda args, _ctx: f"你好，{args.get('name', '朋友')}。这是 HashMM 示例插件。",
        },
    ]
