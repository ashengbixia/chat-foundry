from langchain.tools import tool
from langchain.agents import create_agent

@tool
def tool1(device_id:str)->str:
    temperatures = {"A": 26.5, "B": 58.0}
    if device_id not in temperatures:
        return "不存在"
    return f"设备{device_id}的温度是{temperatures[device_id]}℃"

agent=create_agent(
    model='gpt-4.1-mini',
    tools=[tool1,],
    system_prompt='你是设备助手，查询温度必须调用工具，不得编造数据。'
)

result=agent.invoke({
    'messages':[{"role":"user","content":"查询B设备温度"}],
},config={"recursion_limit":10}
)
print(result["messages"][-1].content)

from langchain.tools import tool
from langchain.agents import create_agent
@tool
def tool1(id:str)->str:
    return
 agent=create_agent(model='gpt-4.1-mini',)
