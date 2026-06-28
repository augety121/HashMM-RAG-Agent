"""Example plugin — demonstrates the plugin architecture."""

def register_tools():
    return [
        {
            "name": "hello_plugin",
            "description": "示例插件工具：返回问候语",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要问候的名字"}
                },
                "required": ["name"],
            },
            "executor": lambda args, ctx: f"你好 {args.get('name', '世界')}！这是来自 example_plugin 的问候。",
        },
    ]
