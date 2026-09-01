CALCULATOR_TOOL = {
    "type": "function",
    "function":{
        "name": "calculator",
        "description": "计算一个算数表达式",
        "parameters": {
            "type": "object",
            "properties":{
                "expression":{
                    "type": "string",
                    "description": "要计算的算数表达式"

                }
            },
            "required": ["expression"],
        },
    },
}