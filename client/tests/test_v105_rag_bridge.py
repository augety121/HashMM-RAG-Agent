"""V105 — RAG 桥接：渠道消息 → Agent → 答复。注入假答复实现，验证多轮/隔离/兜底。"""
import asyncio

from hashmm.channels import rag_bridge as br


def test_bridge_flow():
    async def main():
        seen = []

        async def fake(text, *, conv_id, user_id, history, mobile):
            seen.append(list(history))
            return {"text": f"答:{text}", "sources": [{"filename": "a.pdf"}]}

        br.set_answer_fn(fake)
        br.reset_history()

        # 1) 单条 + 透传 sources
        out = await br.answer("第一问", channel="feishu", peer_id="ou_x")
        assert out["text"] == "答:第一问" and out["sources"] == [{"filename": "a.pdf"}]
        assert seen[0] == []

        # 2) 多轮续上下文
        await br.answer("第二问", channel="feishu", peer_id="ou_x")
        assert seen[1] == [{"role": "user", "content": "第一问"},
                           {"role": "assistant", "content": "答:第一问"}]

        # 3) 不同对端隔离
        await br.answer("别人问", channel="feishu", peer_id="ou_y")
        assert seen[2] == []

        # 4) 空文本不调
        out4 = await br.answer("   ", channel="feishu", peer_id="ou_x")
        assert out4["text"] == ""

        # 5) 实现抛异常 → 兜底
        async def boom(text, **k):
            raise RuntimeError("x")
        br.set_answer_fn(boom)
        out5 = await br.answer("会炸吗", channel="wechat", peer_id="u1")
        assert out5["text"] == br._FALLBACK

        br.set_answer_fn(None)

    asyncio.run(main())


if __name__ == "__main__":
    test_bridge_flow()
    print("test_v105_rag_bridge: all passed")
