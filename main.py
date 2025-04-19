import streamlit as st
from langchain_core.messages import HumanMessage

from chat_bot.llm_processor import LLMProcessor

IS_CHAT_MODE = True

def chat_bot():
    """
    使用 streamlit demo
    """
    graph_key = "zi-wei-graph"

    if graph_key not in st.session_state:
        st.session_state[graph_key] = LLMProcessor().set_kernel_pipeline()
    lang_graph = st.session_state[graph_key]
    lang_config = {"configurable": {"thread_id": "1", "session_id": "zi-wei"}}


    st.title("紫葳斗數算命 GPT")
    st.container()

    if "messages" not in st.session_state:
        st.session_state["messages"] = [
            {"role":
                "assistant", "content": "HIHI, 請跟我說一下你的國曆出生年月日和時間, 我會轉換成農曆並幫你排盤"
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


if __name__ == "__main__":
    if IS_CHAT_MODE:
        chat_bot()
    else:
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
