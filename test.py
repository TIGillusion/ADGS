# import requests
# import json
# import time

# API_URL = "http://8.138.131.119:9000/v1/chat/completions" # 或 OpenAI 地址
# API_KEY = "sk-mykey"

# # 1. 定义工具
# tools_definition = [
#     {
#         "type": "function",
#         "function": {
#             "name": "get_current_weather",
#             "description": "获取指定城市的当前天气",
#             "parameters": {
#                 "type": "object",
#                 "properties": {
#                     "location": {"type": "string", "description": "城市名称"},
#                 },
#                 "required": ["location"],
#             },
#         },
#     }
# ]

# # 2. 模拟本地工具
# def get_current_weather(location):
#     print(f" -> [本地执行] 正在查询 {location} 的天气...")
#     time.sleep(1) # 模拟耗时
#     return json.dumps({"location": location, "temperature": "25°C", "info": "晴天"})

# def stream_call_llm(messages):
#     headers = {
#         "Content-Type": "application/json",
#         "Authorization": f"Bearer {API_KEY}"
#     }
    
#     payload = {
#         "model": "qwen3.6-27b",
#         "messages": messages,
#         "tools": tools_definition,
#         "stream": True # 关键：开启流式
#     }

#     response = requests.post(API_URL, headers=headers, json=payload, stream=True)
    
#     # 手动处理 SSE 流
#     for line in response.iter_lines():
#         if line:
#             decoded_line = line.decode('utf-8')
#             if decoded_line.startswith("data: "):
#                 data_str = decoded_line[6:] # 去掉 "data: "
#                 if data_str.strip() == "[DONE]":
#                     break
                
#                 chunk = json.loads(data_str)
#                 delta = chunk["choices"][0]["delta"]
                
#                 # 关键逻辑：yield delta，让外部处理拼接
#                 yield delta

# # ==========================================
# # 主流程
# # ==========================================

# messages = [{"role": "user", "content": "帮我查一下北京天气"}]

# print("1. 发送流式请求...")

# # 容器：用于拼接工具调用的参数
# tool_calls_buffer = {} 

# # 第一步：接收流并拼接参数
# for delta in stream_call_llm(messages):
#     print(delta)
#     # 检查是否有 tool_calls 字段
#     if "tool_calls" in delta:
#         for tool_call_chunk in delta["tool_calls"]:
#             idx = tool_call_chunk["index"] # 使用 index 区分不同工具
            
#             # 如果是这个工具的第一个片段（初始化）
#             if idx not in tool_calls_buffer:
#                 tool_calls_buffer[idx] = {
#                     "id": tool_call_chunk.get("id", ""),
#                     "name": "", 
#                     "arguments": ""
#                 }
            
#             # 拼接内容
#             # 注意：OpenAI 格式中，name 可能为空，只在第一个片段出现；arguments 是字符串片段
#             if "name" in tool_call_chunk["function"]:
#                 tool_calls_buffer[idx]["name"] = tool_call_chunk["function"]["name"]
                
#             if "arguments" in tool_call_chunk["function"]:
#                 tool_calls_buffer[idx]["arguments"] += tool_call_chunk["function"]["arguments"]

# # 流结束，打印拼接结果
# if tool_calls_buffer:
#     print("2. 流接收结束，解析到的完整工具调用:")
    
#     # 将 buffer 转为 list
#     final_tool_calls = list(tool_calls_buffer.values())
    
#     for tc in final_tool_calls:
#         print(f"   - 工具名: {tc['name']}")
#         print(f"   - 参数JSON: {tc['arguments']}")
        
#         # 3. 本地执行
#         args = json.loads(tc['arguments'])
#         if tc['name'] == "get_current_weather":
#             result = get_current_weather(**args)
            
#             # 4. 构造回传消息
#             # 必须将之前的 assistant 消息（包含tool_calls）加入历史
#             messages.append({
#                 "role": "assistant",
#                 "tool_calls": [{
#                     "id": tc['id'],
#                     "type": "function",
#                     "function": {
#                         "name": tc['name'],
#                         "arguments": tc['arguments']
#                     }
#                 }]
#             })
            
#             # 加入工具执行结果
#             messages.append({
#                 "role": "tool",
#                 "tool_call_id": tc['id'],
#                 "name": tc['name'],
#                 "content": result
#             })

#     # 5. 再次请求获取最终答案
#     print("3. 发送工具结果，请求最终回答...")
#     # 这一步可以是普通请求，也可以是流式请求
#     final_res = requests.post(API_URL, headers={
#         "Content-Type": "application/json",
#         "Authorization": f"Bearer {API_KEY}"
#     }, json={
#         "model": "qwen3.6-27b",
#         "messages": messages,
#         "stream": False # 最后一步用非流式简单点，或者改成 True
#     })
    
#     final_answer = final_res.json()["choices"][0]["message"]["content"]
#     print(f"最终回复: {final_answer}")

from utils.chat import CHAT, ToolCallInterrupt
import json
# 1. 初始化
chat = CHAT(url="http://8.138.131.119:9000/v1", key="sk-mykey", model="qwen3.6-27b")

# 2. 注册工具 (只需注册一次)
chat.register_tool({
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "获取天气",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市"}
            },
            "required": ["city"]
        }
    }
})

history = []
user_msg = "北京今天天气怎么样？"

# 3. 开始循环对话
while True:
    try:
        print("AI: ", end="")
        # 调用生成器
        for text in chat.word_generater(history, user_msg):
            print(text, end="", flush=True) # 实时打印文本
        
        # 正常结束，退出循环
        break 

    except ToolCallInterrupt as e:
        # 捕获工具调用信号
        print(f"\n[系统]: 需要执行工具: {e.tool_calls}")
        
        # 4. 外部处理工具执行
        results = []
        for tc in e.tool_calls:
            func_name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"])
            
            # 你的业务逻辑
            if func_name == "get_weather":
                real_result = f"{args['city']} 今天25度，晴天" 
            else:
                real_result = "Unknown tool"
            
            results.append({
                "tool_call_id": tc["id"], # 必须带上ID
                "name": func_name,
                "content": real_result
            })

        # 5. 构造回传消息
        tool_messages = chat.build_tool_result_messages(e.tool_calls, results)
        
        # 6. 关键步骤：更新历史记录
        # 将 assistant 的调用记录 和 tool 的结果记录 追加到 history
        history.extend(tool_messages)
        
        # 下一轮循环，user_msg 设为 None 或 ""，因为我们要继续模型生成的逻辑
        user_msg = None # 或者 ""，表示继续上一轮对话
        print("\n[系统]: 工具执行完毕，继续生成...")

