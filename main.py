import streamlit as st
import traceback
from langchain_core.messages import HumanMessage, AIMessage
from dotenv import load_dotenv
from chat_bot.global_config import GlobalConfig
from chat_bot.llm_processor import LLMProcessor

#---line---
from flask import Flask, request
# 載入 json 標準函式庫，處理回傳的資料格式
import json
# 載入 LINE Message API 相關函式庫
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

load_dotenv()

IS_CHAT_MODE = False
IS_LINEBOT_MODE = True

def chat_bot():
    """
    使用 streamlit demo
    """
    graph_key = "zi-wei-graph"

    if graph_key not in st.session_state:
        st.session_state[graph_key] = LLMProcessor().set_kernel_pipeline()
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

        if isinstance(output["messages"][-1], dict):
            cur_msg = output["messages"][-1]["content"]
        else:
            cur_msg = output["messages"][-1].content

        st.session_state.messages.append({"role": "assistant", "content": cur_msg})
        st.chat_message("assistant").write(cur_msg)

def general_mode():
    graph = LLMProcessor().set_kernel_pipeline()
    config = {"configurable": {"thread_id": "1", "session_id": "zi-wei"}}
    while True:
        prompt = input("👤 You: ")  

        output = graph.invoke(
            {"messages": [HumanMessage(prompt)]},
            config=config
        )
        if isinstance(output["messages"][-1], dict):
            cur_msg = output["messages"][-1]["content"]
        else:
            cur_msg = output["messages"][-1].content
        print(cur_msg)

if __name__ == "__main__":
    if IS_CHAT_MODE:
        chat_bot()

    elif not IS_LINEBOT_MODE:
        general_mode()
    else:
        app = Flask(__name__)
        SESSION_STATS = {}
        HOROSCOPE = {}
        @app.route("/", methods=['POST'])
        def linebot():
            """
            line bot, need open ngrok
            """
            graph = LLMProcessor().set_kernel_pipeline()
            body = request.get_data(as_text=True)                    # 取得收到的訊息內容
            try:
                json_data = json.loads(body)                         # json 格式化訊息內容
                access_token = GlobalConfig.LINE_ACCESS_KEY
                secret = GlobalConfig.LINE_SECRET
                line_bot_api = LineBotApi(access_token)              # 確認 token 是否正確
                handler = WebhookHandler(secret)                     # 確認 secret 是否正確
                signature = request.headers['X-Line-Signature']      # 加入回傳的 headers
                handler.handle(body, signature)                      # 綁定訊息回傳的相關資訊
                tk = json_data['events'][0]['replyToken']            # 取得回傳訊息的 Token
                recieve_type = json_data['events'][0]['message']['type']     # 取得 LINe 收到的訊息類型
                if recieve_type =='text':
                    msg = json_data['events'][0]['message']['text']  # 取得 LINE 收到的文字訊息
                    user_id = json_data['events'][0]['source']['userId']
                    # config["configurable"]["session_id"] = session_id

                    config = {
                        "configurable": {
                            "thread_id": user_id,       # 每個使用者自己的 thread
                            "session_id": user_id      # 這個是你原本寫的，不一定要
                        }
                    }
                    
                    # 初始化歷史
                    if user_id not in SESSION_STATS:
                        SESSION_STATS[user_id] = []
                    if user_id not in HOROSCOPE or "/reset" in msg:
                        HOROSCOPE[user_id] = ""
                        SESSION_STATS[user_id] = []
                    # 加入使用者訊息
                    SESSION_STATS[user_id].append(HumanMessage(content=msg))

                    output = graph.invoke(
                        {"messages": SESSION_STATS[user_id], "horoscope": HOROSCOPE[user_id]},
                        config=config
                    )
                    if HOROSCOPE[user_id] == "":
                        HOROSCOPE[user_id] = output["horoscope"]

                    if isinstance(output["messages"][-1], dict):
                        reply = output["messages"][-1]["content"]
                        SESSION_STATS[user_id].append(AIMessage(content=reply))
                    else:
                        reply = output["messages"][-1].content
                        SESSION_STATS[user_id].append(AIMessage(content=reply))
                        print("cur_statue:", SESSION_STATS[user_id])
                else:
                    reply = '我是算命機器人, 看不到文字以外的訊息喔!'
                print(reply)
                line_bot_api.reply_message(tk,TextSendMessage(reply))# 回傳訊息
            except Exception as e:
                print("output['messages'][-1]", (output["messages"][-1]))
                print(traceback.format_exc())
                print(body, e)                                          # 如果發生錯誤，印出收到的內容
            return 'OK'                                              # 驗證 Webhook 使用，不能省略
        app.run()