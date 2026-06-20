"""
ui/styles.py
============
所有 Streamlit 自定义 CSS 集中管理。
视图层其他模块通过 inject_global_css() 调用，不直接写 st.markdown(css)。
"""

import streamlit as st


_GLOBAL_CSS = """
<style>
    /* 压缩主容器顶部内边距 */
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 0.5rem !important;
    }

    /* 隐藏 Streamlit 顶部原生 Header 空白 */
    header[data-testid="stHeader"] {
        height: 0px !important;
        background: transparent !important;
    }

    /* 紧凑化 Metric 卡片间距 */
    div[data-testid="stMetric"] {
        padding-top: 0rem !important;
        padding-bottom: 0rem !important;
    }

    /* 微调标题边距 */
    h3, h5 {
        margin-top: 0.2rem !important;
        margin-bottom: 0.5rem !important;
    }

    /* 日期筛选栏内联说明文字 */
    .date-caption {
        padding-top: 25px;
        color: gray;
    }
</style>
"""


def inject_global_css() -> None:
    """将全局 CSS 注入到 Streamlit 页面。在 app.py 启动时调用一次。"""
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)


def sample_count_html(count: int) -> str:
    """生成当前筛选样本量的 HTML 提示文字。"""
    return (
        f"<div class='date-caption'>"
        f"📊 当前筛选范围内完赛样本量：<b>{count}</b> 场"
        f"</div>"
    )
