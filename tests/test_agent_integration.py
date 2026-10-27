from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.core.streaming import stream_agent_events
from app.core.tools import fitness_tools


class ScriptedChatModel(BaseChatModel):
    responses: list[AIMessage]
    index: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs: Any):
        return self

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        message = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_agent_runs_tool_then_streams_answer() -> None:
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "calculate_bmi",
                        "args": {"height_cm": 175, "weight_kg": 80},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="你的 BMI 是 26.1。"),
        ]
    )
    agent = create_agent(model=model, tools=fitness_tools, system_prompt="测试")
    events = list(stream_agent_events(agent, [("user", "身高175，体重80")]))

    assert any(event["type"] == "tool_call" for event in events)
    assert any(event["type"] == "tool_result" for event in events)
    text = "".join(event["text"] for event in events if event["type"] == "token")
    assert text == "你的 BMI 是 26.1。"
