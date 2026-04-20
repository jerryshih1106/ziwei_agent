import streamlit as st
import traceback
import uuid
from langchain_core.messages import HumanMessage, AIMessage
from dotenv import load_dotenv
from chat_bot.global_config import GlobalConfig
from chat_bot.llm_processor import LLMProcessor

#---line---
from flask import Flask, request
import json
from linebot import LineBotApi, WebhookHandler
from linebot.models import TextSendMessage

load_dotenv()

IS_CHAT_MODE = False
IS_LINEBOT_MODE = True


def _extract_last_message(output: dict) -> str:
    last = output["messages"][-1]
    return last["content"] if isinstance(last, dict) else last.content


def chat_bot():
    """
    使用 streamlit demo
    """
    graph_key = "zi-wei-graph"

    if graph_key not in st.session_state:
        st.session_state[graph_key] = LLMProcessor().set_pipeline()
    lang_graph = st.session_state[graph_key]
    lang_config = {"configurable": {"thread_id": "1", "session_id": "zi-wei"}}


    st.title("算命 GPT")
    st.container()

    if "messages" not in st.session_state:
        st.session_state["messages"] = [
            {"role":
                "assistant", "content": "HIHI～ 請跟我說一下你的國曆出生年月日和時間, 我來幫你排盤"
            }
        ]

    for msg in st.session_state.messages:
        st.chat_message(msg["role"]).write(msg["content"])

    if prompt := st.chat_input(key="chat_input"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        st.chat_message("user").write(prompt)

        output = lang_graph.invoke(
            {"messages": [HumanMessage(prompt)]},
            config=lang_config
        )

        cur_msg = _extract_last_message(output)

        st.session_state.messages.append({"role": "assistant", "content": cur_msg})
        st.chat_message("assistant").write(cur_msg)

def general_mode():
    graph = LLMProcessor().set_pipeline()
    config = {"configurable": {"thread_id": "1", "session_id": "zi-wei"}}
    while True:
        prompt = input("👤 You: ")

        output = graph.invoke(
            {"messages": [HumanMessage(prompt)]},
            config=config
        )
        cur_msg = _extract_last_message(output)
        print(cur_msg)

if __name__ == "__main__":
    if IS_CHAT_MODE:
        chat_bot()

    elif not IS_LINEBOT_MODE:
        general_mode()
    else:
        app = Flask(__name__)

        # Pipeline 在啟動時建立一次，讓 MemorySaver 可跨 request 持續存活
        _pipeline = LLMProcessor().set_pipeline()

        # LangGraph 模式：手動管理每個使用者的訊息歷史與命盤
        SESSION_STATS: dict = {}
        HOROSCOPE: dict = {}

        # Agent skill 模式：每個使用者對應一個 thread_id（/reset 時換新的）
        AGENT_THREAD: dict = {}

        @app.route("/", methods=['POST'])
        def linebot():
            """
            LINE Bot route（同時支援 agent skill 模式與 langgraph 模式）。
            需先開啟 ngrok。
            """
            body = request.get_data(as_text=True)
            try:
                json_data = json.loads(body)
                line_bot_api = LineBotApi(GlobalConfig.LINE_ACCESS_KEY)
                handler = WebhookHandler(GlobalConfig.LINE_SECRET)
                handler.handle(body, request.headers['X-Line-Signature'])
                event = json_data['events'][0]
                tk = event['replyToken']

                if event['message']['type'] != 'text':
                    reply = '我是算命機器人, 看不到文字以外的訊息喔!'
                    line_bot_api.reply_message(tk, TextSendMessage(reply))
                    return 'OK'

                msg = event['message']['text']
                user_id = event['source']['userId']
                is_reset = "/reset" in msg

                if GlobalConfig.USE_AGENT_SKILL:
                    # ── Agent skill 模式 ──────────────────────────────────────
                    # /reset 或第一次見到此 user → 派發新的 thread_id
                    if is_reset or user_id not in AGENT_THREAD:
                        AGENT_THREAD[user_id] = str(uuid.uuid4())
                        if is_reset:
                            line_bot_api.reply_message(
                                tk, TextSendMessage('已重置對話！請重新告訴我你的出生資料。')
                            )
                            return 'OK'

                    config = {"configurable": {"thread_id": AGENT_THREAD[user_id]}}
                    output = _pipeline.invoke(
                        {"messages": [HumanMessage(content=msg)]},
                        config=config,
                    )
                    reply = _extract_last_message(output)

                else:
                    # ── LangGraph 固定流程模式 ────────────────────────────────
                    config = {"configurable": {"thread_id": user_id, "session_id": user_id}}
                    if user_id not in SESSION_STATS:
                        SESSION_STATS[user_id] = []
                    if user_id not in HOROSCOPE or is_reset:
                        HOROSCOPE[user_id] = ""
                        SESSION_STATS[user_id] = []

                    SESSION_STATS[user_id].append(HumanMessage(content=msg))
                    output = _pipeline.invoke(
                        {"messages": SESSION_STATS[user_id], "horoscope": HOROSCOPE[user_id]},
                        config=config,
                    )
                    if not HOROSCOPE[user_id]:
                        HOROSCOPE[user_id] = output.get("horoscope", "")

                    reply = _extract_last_message(output)
                    SESSION_STATS[user_id].append(AIMessage(content=reply))
                    print("cur_state:", SESSION_STATS[user_id])

                print(reply)
                line_bot_api.reply_message(tk, TextSendMessage(reply))

            except Exception as e:
                print(traceback.format_exc())
                print(body, e)
            return 'OK'

        app.run()