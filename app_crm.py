"""Streamlit console for the Amazon influencer private-domain CRM."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from modules.ai_agent import InfluencerAIAgent
from modules.db_crm import (
    COLLAB_STATUS_PENDING,
    CRMDatabaseManager,
    VALID_COLLAB_STATUSES,
)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_API_KEY = (
    "sk-343a68b773eb21bf1e4fbd01d91e91c2ddc4b0710fabfcee4c1c59d774695402"
)
DEFAULT_BASE_URL = "http://sub2api.aiteyixia.cn/v1"
DEFAULT_MODEL = "gpt-5.5"
NEW_WORKBENCH_COLLAB = "➕ 新建红人对话 / 等待提取"

# ---------------------------------------------------------------------------
# Page config & styling
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Influencer CRM",
    page_icon="🤝",
    layout="wide",
)

st.markdown(
    """
    <style>
        .block-container { padding-top: 1.5rem; max-width: 1400px; }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%);
        }
        .crm-header {
            font-size: 1.75rem;
            font-weight: 700;
            color: #1e293b;
            margin-bottom: 0.25rem;
        }
        .crm-subtitle { color: #64748b; margin-bottom: 1.5rem; }
        div[data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            padding: 0.75rem 1rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------

if "api_key" not in st.session_state:
    st.session_state.api_key = DEFAULT_API_KEY
if "base_url" not in st.session_state:
    st.session_state.base_url = DEFAULT_BASE_URL
if "model_name" not in st.session_state:
    st.session_state.model_name = DEFAULT_MODEL
if "current_draft" not in st.session_state:
    st.session_state.current_draft = None
if "current_extracted_info" not in st.session_state:
    st.session_state.current_extracted_info = None
if "timeline_chat_history" not in st.session_state:
    st.session_state.timeline_chat_history = ""
if "current_collab_id" not in st.session_state:
    st.session_state.current_collab_id = None
if "current_influencer_id" not in st.session_state:
    st.session_state.current_influencer_id = None
if "last_selected_collab" not in st.session_state:
    st.session_state.last_selected_collab = NEW_WORKBENCH_COLLAB
if "current_collab_status" not in st.session_state:
    st.session_state.current_collab_status = COLLAB_STATUS_PENDING
if "selected_collab" not in st.session_state:
    st.session_state.selected_collab = None
if "selected_collab_id" not in st.session_state:
    st.session_state.selected_collab_id = None

db = CRMDatabaseManager()


def get_ai_agent() -> InfluencerAIAgent | None:
    """Build an AI agent from sidebar credentials, or return None if incomplete."""
    api_key = st.session_state.get("api_key", "").strip()
    if not api_key:
        return None
    return InfluencerAIAgent(
        api_key=api_key,
        base_url=st.session_state.get("base_url") or None,
        model=st.session_state.get("model_name") or DEFAULT_MODEL,
    )


def product_label(product: dict[str, Any]) -> str:
    """Format a product for select-box display."""
    return f"{product['name']} ({product['asin']})"


def today_label() -> str:
    """Return today's date string for timeline markers."""
    return datetime.now().strftime("%Y-%m-%d")


def build_draft_chat_history(history: str, current_message: str) -> str:
    """Combine archived history with the current inbound message for AI context."""
    history = (history or "").strip()
    current = current_message.strip()
    if history:
        return f"{history}\n\n[Influencer]: {current}"
    return f"[Influencer]: {current}"


def append_timeline_exchange(
    history: str,
    influencer_message: str,
    brand_reply: str,
    date_label: str | None = None,
) -> str:
    """Append a dated influencer/brand exchange to the collaboration timeline."""
    date_label = date_label or today_label()
    influencer_block = f"[Influencer {date_label}]:\n{influencer_message.strip()}"
    brand_block = f"[Brand {date_label}]:\n{brand_reply.strip()}"
    history = (history or "").strip()
    if history:
        return f"{history}\n\n{influencer_block}\n\n{brand_block}"
    return f"{influencer_block}\n\n{brand_block}"


def apply_current_draft(draft: dict[str, str]) -> None:
    """Store bilingual draft and clear editable widget cache for refresh on rerun."""
    st.session_state.current_draft = draft
    if "edited_english" in st.session_state:
        del st.session_state["edited_english"]


def parse_chat_history_to_blocks(history_str: str) -> list[dict[str, Any]]:
    """Parse the raw chat history string into a list of structured message blocks."""
    if not history_str or not history_str.strip():
        return []

    pattern = (
        r"=======\s*(?:👤|💼)?\s*\[?"
        r"(红人来信 Influencer|品牌回复 Brand)\]?\s*\|\s*(.*?)\s*=======\n"
    )

    if "=======" not in history_str:
        return [{
            "id": str(uuid.uuid4()),
            "role": "红人来信 Influencer",
            "date": "",
            "content": history_str.strip(),
        }]

    blocks: list[dict[str, Any]] = []
    parts = re.split(pattern, history_str)

    if parts[0].strip():
        blocks.append({
            "id": str(uuid.uuid4()),
            "role": "红人来信 Influencer",
            "date": "",
            "content": parts[0].strip(),
        })

    for i in range(1, len(parts), 3):
        if i + 2 >= len(parts):
            break
        role_str = parts[i]
        date_str = parts[i + 1]
        content_str = parts[i + 2]
        role = "红人来信 Influencer" if "红人" in role_str else "品牌回复 Brand"
        blocks.append({
            "id": str(uuid.uuid4()),
            "role": role,
            "date": date_str.strip(),
            "content": content_str.strip(),
        })

    return blocks


def build_history_string_from_blocks(blocks: list[dict[str, Any]]) -> str:
    """Reconstruct the chat history string from structured message blocks."""
    lines: list[str] = []
    for block in blocks:
        content = block.get("content", "").strip()
        if not content:
            continue
        role = block.get("role", "红人来信 Influencer")
        date_str = block.get("date", "") or datetime.now().strftime("%Y-%m-%d %H:%M")
        icon = "👤" if "红人" in role else "💼"
        header = f"======= {icon} [{role}] | {date_str} ======="
        lines.append(header)
        lines.append(content)
        lines.append("")
    return "\n".join(lines).strip()


def sync_chat_blocks_from_widgets(blocks: list[dict[str, Any]]) -> None:
    """Sync block dicts from bound widget session keys before save/preview."""
    for block in blocks:
        block_id = block["id"]
        role_key = f"role_{block_id}"
        content_key = f"content_{block_id}"
        if role_key in st.session_state:
            block["role"] = st.session_state[role_key]
        if content_key in st.session_state:
            block["content"] = st.session_state[content_key]


def clear_chat_block_widget_keys(block_id: str) -> None:
    """Remove widget session keys for a deleted chat block."""
    for key in (f"role_{block_id}", f"content_{block_id}"):
        if key in st.session_state:
            del st.session_state[key]


def build_chat_context(message_text: str) -> str:
    """Build full chat context from timeline history plus current inbound message."""
    history = st.session_state.get("timeline_chat_history") or ""
    return build_draft_chat_history(history, message_text)


def build_revision_prompt(current_english: str, feedback: str) -> str:
    """Build a revision prompt that preserves the previous draft as context."""
    return f"""
---
I have a previous draft of the email:
{current_english}

Please revise the above email draft strictly based on the following feedback:
{feedback}
"""


def run_draft_generation(
    agent: InfluencerAIAgent,
    product_info: dict[str, Any],
    message_text: str,
    custom_prompt: str = "",
) -> dict[str, str]:
    """Build chat context and call the AI agent to produce a bilingual draft."""
    extracted_info = st.session_state.current_extracted_info or {}
    history = st.session_state.get("timeline_chat_history") or ""
    full_chat_history = build_draft_chat_history(history, message_text)
    return agent.draft_reply(
        product_info=product_info,
        chat_history=full_chat_history,
        extracted_info=extracted_info,
        custom_prompt=custom_prompt,
    )


def clear_workbench_widget_cache() -> None:
    """Delete widget-bound session keys so inputs reset cleanly on rerun."""
    keys_to_clear = ["inbound_message", "edited_english", "current_draft"]
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]


def render_sidebar() -> None:
    """Render API configuration panel."""
    with st.sidebar:
        st.markdown("### 🔑 API 密钥配置")
        st.text_input(
            "API Key",
            type="password",
            key="api_key",
            value=DEFAULT_API_KEY,
            help="OpenAI 或兼容接口的 API Key",
        )
        st.text_input(
            "Base URL（可选）",
            key="base_url",
            value=DEFAULT_BASE_URL,
            help="留空则使用 OpenAI 官方地址；可填 OneAPI / Azure 等兼容网关",
        )
        st.text_input(
            "模型名称",
            key="model_name",
            value=DEFAULT_MODEL,
        )

        st.divider()
        st.caption("Influencer CRM · Phase 2")
        if not st.session_state.get("api_key", "").strip():
            st.warning("请先在侧边栏配置 API Key，以启用 AI 功能。")


def render_products_tab() -> None:
    """Product library management."""
    st.subheader("📁 产品库管理")
    st.caption("预置 ASIN、卖点与谈判策略，供 AI 起草邮件时引用。")

    with st.form("add_product_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            asin = st.text_input("ASIN *", placeholder="B0XXXXXXXX")
            name = st.text_input("产品简称 *", placeholder="睡袋 / 易拉宝")
            price = st.number_input("价格 (USD)", min_value=0.0, step=0.01, format="%.2f")
        with col2:
            selling_points = st.text_area("五点描述 / 核心卖点", height=120)
            pros_cons = st.text_area("优缺点（客诉应对）", height=80)
            negotiation_strategy = st.text_input(
                "谈判底牌", placeholder="20% 佣金 + 15% 折扣码"
            )

        submitted = st.form_submit_button("✅ 添加产品", use_container_width=True)
        if submitted:
            if not asin.strip() or not name.strip():
                st.error("ASIN 与产品简称不能为空。")
            else:
                try:
                    db.add_product(
                        asin=asin.strip().upper(),
                        name=name.strip(),
                        price=price or None,
                        selling_points=selling_points.strip() or None,
                        pros_cons=pros_cons.strip() or None,
                        negotiation_strategy=negotiation_strategy.strip() or None,
                    )
                    st.success(f"产品「{name.strip()}」已成功入库。")
                    st.rerun()
                except Exception as exc:
                    st.error(f"添加失败：{exc}")

    st.divider()
    products = db.get_all_products()
    if products:
        df = pd.DataFrame(products)
        display_cols = [
            "id", "asin", "name", "price",
            "selling_points", "negotiation_strategy", "created_at",
        ]
        st.dataframe(
            df[[c for c in display_cols if c in df.columns]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("暂无产品，请先通过上方表单录入。")


def build_extracted_info_from_collab(collab: dict[str, Any]) -> dict[str, Any]:
    """Map collaboration influencer fields to AI workbench extracted_info schema."""
    return {
        "name": collab.get("influencer_name") or "",
        "email": collab.get("influencer_email") or None,
        "shipping_address": collab.get("influencer_shipping_address") or None,
        "phone": collab.get("influencer_phone") or None,
        "social_links": collab.get("influencer_social_links") or "",
    }


def reset_workbench_collab_context() -> None:
    """Clear workbench session context for a fresh influencer conversation."""
    st.session_state.current_collab_id = None
    st.session_state.current_influencer_id = None
    st.session_state.timeline_chat_history = ""
    st.session_state.current_collab_status = COLLAB_STATUS_PENDING
    st.session_state.current_extracted_info = None


def apply_workbench_collab_context(collab: dict[str, Any]) -> None:
    """Load an existing collaboration into the AI workbench session context."""
    st.session_state.current_collab_id = collab["id"]
    st.session_state.current_influencer_id = collab.get("influencer_id")
    st.session_state.timeline_chat_history = collab.get("chat_history") or ""
    st.session_state.current_collab_status = collab.get("status") or COLLAB_STATUS_PENDING
    st.session_state.current_extracted_info = build_extracted_info_from_collab(collab)


def sync_workbench_collab_selection(
    selected_label: str,
    collab_by_label: dict[str, dict[str, Any]],
) -> None:
    """Reset or load workbench context when the collab selector changes."""
    st.session_state.last_selected_collab = selected_label

    if selected_label == NEW_WORKBENCH_COLLAB:
        reset_workbench_collab_context()
        st.rerun()

    apply_workbench_collab_context(collab_by_label[selected_label])
    st.rerun()


def render_ai_workbench_tab() -> None:
    """AI-powered message extraction and reply drafting with timeline closure."""
    st.subheader("🤖 AI 沟通工作台")
    st.caption("粘贴站内信 → 提取红人 → 生成回复 → 确认归档，形成完整会话闭环。")

    products = db.get_all_products()
    if not products:
        st.warning("请先在「产品库管理」中录入至少一个产品。")
        return

    product_options = {product_label(p): p for p in products}
    selected_label = st.selectbox(
        "当前沟通产品",
        options=list(product_options.keys()),
        key="selected_product_label",
    )
    selected_product: dict[str, Any] = product_options[selected_label]

    all_collaborations = db.get_all_collaborations()
    collab_options = [NEW_WORKBENCH_COLLAB] + [
        f"{collab['influencer_name']} · {collab['product_name']}"
        for collab in all_collaborations
    ]
    collab_by_label = {
        f"{collab['influencer_name']} · {collab['product_name']}": collab
        for collab in all_collaborations
    }

    selected_collab_label = st.selectbox(
        "关联历史红人合作",
        options=collab_options,
        key="workbench_collab_selector",
        help="选择「新建」清空工作台；选择已有记录加载历史上下文。",
    )

    if selected_collab_label != st.session_state.get("last_selected_collab"):
        sync_workbench_collab_selection(selected_collab_label, collab_by_label)

    if (
        selected_collab_label != NEW_WORKBENCH_COLLAB
        and st.session_state.get("current_collab_id")
    ):
        st.caption(
            f"已关联合作记录 ID: {st.session_state.current_collab_id} · "
            f"历史消息已加载，可直接生成回复或继续归档。"
        )

    if st.session_state.timeline_chat_history:
        with st.expander("📜 聊天记录时间线（已加载历史）", expanded=True):
            st.text_area(
                "Timeline",
                value=st.session_state.timeline_chat_history,
                height=200,
                disabled=True,
                label_visibility="collapsed",
            )
    elif st.session_state.current_collab_id:
        st.caption("该红人暂无历史聊天记录，归档后将从此处开始构建时间线。")

    message_text = st.text_area(
        "粘贴红人站内信 / 邮件原文",
        height=260,
        placeholder="Hi, I'm interested in reviewing your sleeping bag...",
        key="inbound_message",
    )

    system_prompt = st.text_area(
        "回复风格系统提示词（可选）",
        height=100,
        placeholder="例如：语气友好专业，强调长期合作，不主动承诺超过 20% 佣金。",
        key="reply_system_prompt",
    )

    if st.session_state.current_extracted_info:
        known_email = st.session_state.current_extracted_info.get("email")
        if known_email:
            st.info(
                f"✅ 已识别红人邮箱：**{known_email}** — "
                "生成回复时将不再索要此信息。"
            )
        else:
            st.info(
                "✅ 已提取红人信息（暂无邮箱）— "
                "生成回复时将参考已知字段，避免重复询问。"
            )

    col_extract, col_draft = st.columns(2)

    with col_extract:
        if st.button("🔍 一键提取红人信息并入库", use_container_width=True):
            if not message_text.strip():
                st.error("请先粘贴站内信内容。")
            else:
                agent = get_ai_agent()
                if agent is None:
                    st.error("请先在侧边栏配置 API Key。")
                else:
                    try:
                        with st.spinner("AI 正在解析红人信息..."):
                            extracted = agent.extract_influencer_info(message_text)

                        target_influencer_id = None
                        if st.session_state.get("current_collab_id"):
                            target_influencer_id = st.session_state.get("current_influencer_id")

                        influencer_id = db.upsert_influencer(
                            name=extracted["name"],
                            email=extracted["email"],
                            social_links=extracted["social_links"],
                            shipping_address=extracted["shipping_address"],
                            phone=extracted["phone"],
                            influencer_id=target_influencer_id,
                        )
                        st.session_state.current_influencer_id = influencer_id

                        existing = db.find_collaboration(
                            influencer_id, selected_product["id"]
                        )
                        if existing:
                            collab_id = existing["id"]
                            st.session_state.timeline_chat_history = (
                                existing.get("chat_history") or ""
                            )
                            st.session_state.current_collab_status = existing["status"]
                        else:
                            collab_id = db.create_collaboration(
                                influencer_id=influencer_id,
                                product_id=selected_product["id"],
                            )
                            st.session_state.timeline_chat_history = ""
                            st.session_state.current_collab_status = COLLAB_STATUS_PENDING

                        st.session_state.current_collab_id = collab_id
                        st.session_state.current_extracted_info = extracted

                        email_hint = extracted.get("email")
                        history_note = (
                            "已加载历史聊天记录。"
                            if st.session_state.timeline_chat_history.strip()
                            else "暂无历史记录，确认归档后将开始构建时间线。"
                        )
                        if email_hint:
                            st.success(
                                f"红人已入库（ID: {influencer_id}），"
                                f"合作记录 ID: {collab_id}。"
                                f" 已识别到红人邮箱：**{email_hint}**，"
                                f"生成回复时将不再索要。{history_note}"
                            )
                        else:
                            st.success(
                                f"红人已入库（ID: {influencer_id}），"
                                f"合作记录 ID: {collab_id}。"
                                f" 暂未识别到邮箱。{history_note}"
                            )
                    except Exception as exc:
                        st.error(f"提取失败：{exc}")

    with col_draft:
        if st.button("✉️ 生成专属英文回复", use_container_width=True):
            if not message_text.strip():
                st.error("请先粘贴站内信 / 聊天记录。")
            else:
                agent = get_ai_agent()
                if agent is None:
                    st.error("请先在侧边栏配置 API Key。")
                else:
                    try:
                        with st.spinner("AI 正在起草双语回复..."):
                            draft = run_draft_generation(
                                agent=agent,
                                product_info=selected_product,
                                message_text=message_text,
                                custom_prompt=system_prompt,
                            )
                        apply_current_draft(draft)

                        extracted_info = st.session_state.current_extracted_info or {}
                        if extracted_info.get("email"):
                            st.success(
                                f"双语草稿已生成（已携带邮箱 {extracted_info['email']}，"
                                "不会重复索要）。可在下方微调英文后归档。"
                            )
                        else:
                            st.success("双语草稿已生成，可在下方微调英文后归档。")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"生成失败：{exc}")

    if st.session_state.current_extracted_info:
        st.markdown("**提取结果预览**")
        st.json(st.session_state.current_extracted_info)

    if st.session_state.current_draft:
        st.markdown("**双语回复草稿**")
        st.caption("左侧英文可微调并归档；右侧中文仅供运营参考。")

        draft = st.session_state.current_draft
        english_text = draft.get("english_draft", "")
        chinese_text = draft.get("chinese_translation", "")

        if "edited_english" not in st.session_state:
            st.session_state.edited_english = english_text

        col1, col2 = st.columns(2)
        with col1:
            st.text_area(
                "📝 英文回复草稿",
                value=english_text,
                height=300,
                key="edited_english",
            )
        with col2:
            st.text_area(
                "🇨🇳 中文翻译参考",
                value=chinese_text,
                height=300,
                disabled=True,
            )

        feedback = st.text_input(
            "✍️ 优化意见 (中文)：",
            placeholder="例如：语气更热情一点，并提醒对方记得发视频链接",
        )

        col_regen, col_archive = st.columns(2)
        with col_regen:
            if st.button("🔄 根据意见重新生成", use_container_width=True):
                if not feedback.strip():
                    st.warning("⚠️ 请先输入优化意见！")
                elif not message_text.strip():
                    st.error("请先粘贴站内信 / 聊天记录。")
                else:
                    agent = get_ai_agent()
                    if agent is None:
                        st.error("请先在侧边栏配置 API Key。")
                    else:
                        with st.spinner("正在根据您的意见光速重写..."):
                            current_english = st.session_state.get("edited_english", "")
                            revision_prompt = build_revision_prompt(
                                current_english, feedback.strip()
                            )
                            chat_context = build_chat_context(message_text)
                            try:
                                new_draft_dict = agent.draft_reply(
                                    product_info=selected_product,
                                    chat_history=chat_context,
                                    extracted_info=(
                                        st.session_state.current_extracted_info or {}
                                    ),
                                    custom_prompt=revision_prompt,
                                )

                                st.session_state.current_draft = new_draft_dict

                                if "edited_english" in st.session_state:
                                    del st.session_state["edited_english"]

                                st.rerun()
                            except Exception as exc:
                                st.error(f"❌ 重新生成失败: {exc}")

        with col_archive:
            if st.button("💾 确认并录入聊天记录", type="primary", use_container_width=True):
                collab_id = st.session_state.get("current_collab_id")
                if not collab_id:
                    st.error("请先点击「一键提取红人信息并入库」以关联合作记录。")
                else:
                    with st.spinner("正在将记录写入数据库..."):
                        try:
                            final_english = st.session_state.get("edited_english", "")
                            inbound_msg = st.session_state.get("inbound_message", "")

                            if not final_english or not str(final_english).strip():
                                st.warning("⚠️ 没有可录入的回复草稿！")
                            elif not inbound_msg or not str(inbound_msg).strip():
                                st.warning("⚠️ 站内信内容为空，无法归档。")
                            else:
                                current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
                                chat_context = (
                                    st.session_state.get("timeline_chat_history") or ""
                                )
                                new_chat_segment = f"""
======= 👤 [红人来信 Influencer] | {current_date} =======
{inbound_msg.strip()}

======= 💼 [品牌回复 Brand] | {current_date} =======
{str(final_english).strip()}
"""
                                updated_chat_history = (
                                    chat_context + "\n" + new_chat_segment.strip() + "\n"
                                )

                                updated = db.update_collaboration_status(
                                    collaboration_id=int(collab_id),
                                    status=st.session_state.current_collab_status,
                                    chat_history=updated_chat_history,
                                    last_interaction_date=datetime.now(),
                                )
                                if not updated:
                                    st.error("❌ 归档失败：未找到对应合作记录。")
                                else:
                                    st.session_state.timeline_chat_history = (
                                        updated_chat_history
                                    )
                                    clear_workbench_widget_cache()
                                    st.success(
                                        "✅ 已成功归档到该红人的时间线！界面即将重置..."
                                    )
                                    st.rerun()
                        except Exception as exc:
                            st.error(f"❌ 归档失败: {exc}")


def compute_profile_status(email: str, address: str) -> str:
    """Return a visual profile completeness indicator for dashboard display."""
    if email and address:
        return "🟢 完整"
    if email and not address:
        return "🟡 缺地址"
    return "🔴 缺关键信息"


def get_collab_avatar_blob(collab: dict[str, Any]) -> bytes | None:
    """Return avatar binary data stored on a collaboration record."""
    data = collab.get("influencer_avatar_blob") or collab.get("avatar_blob")
    if data is None:
        return None
    if isinstance(data, memoryview):
        return bytes(data)
    if isinstance(data, bytes):
        return data
    return None


def build_dashboard_display_df(raw_data_list: list[dict[str, Any]]) -> pd.DataFrame:
    """Annotate profile status and build a business-friendly dashboard DataFrame."""
    for row in raw_data_list:
        email = row.get("influencer_email") or ""
        address = row.get("influencer_shipping_address") or ""
        row["资料状态"] = compute_profile_status(email, address)
        row["avatar_display"] = (
            "🖼️ 已设置" if get_collab_avatar_blob(row) else "—"
        )

    df = pd.DataFrame(raw_data_list)
    if df.empty:
        return pd.DataFrame()

    core_columns = [
        "资料状态",
        "status",
        "product_name",
        "avatar_display",
        "influencer_name",
        "influencer_email",
        "influencer_shipping_address",
        "influencer_social_links",
        "influencer_phone",
        "order_number",
        "tracking_number",
        "assigned_to",
    ]
    display_df = df[[col for col in core_columns if col in df.columns]].rename(
        columns={
            "status": "合作状态",
            "product_name": "合作产品",
            "avatar_display": "头像",
            "influencer_name": "红人姓名",
            "influencer_email": "邮箱",
            "influencer_shipping_address": "收货地址",
            "influencer_social_links": "社媒链接",
            "influencer_phone": "电话",
            "order_number": "订单号",
            "tracking_number": "追踪单号",
            "assigned_to": "跟进人",
        }
    )
    return display_df


def sync_selected_collab(raw_data_list: list[dict[str, Any]]) -> None:
    """Refresh selected_collab from latest DB snapshot by collaboration id."""
    selected_id = st.session_state.get("selected_collab_id")
    if not selected_id:
        return
    for row in raw_data_list:
        if row["id"] == selected_id:
            st.session_state.selected_collab = row
            return


def init_collab_detail_state(current_collab: dict[str, Any]) -> None:
    """Reset profile and history widgets when dashboard selection changes."""
    collab_id = current_collab["id"]
    if st.session_state.get("dashboard_detail_collab_id") == collab_id:
        return

    st.session_state.dashboard_detail_collab_id = collab_id
    st.session_state.dashboard_profile_collab_id = None

    profile_keys = (
        f"edit_name_{collab_id}",
        f"edit_email_{collab_id}",
        f"edit_address_{collab_id}",
        f"edit_phone_{collab_id}",
        f"edit_social_{collab_id}",
        f"edit_tags_{collab_id}",
        f"avatar_upload_{collab_id}",
    )
    for key in profile_keys:
        if key in st.session_state:
            del st.session_state[key]

    history_key = f"edit_history_{collab_id}"
    if history_key in st.session_state:
        del st.session_state[history_key]

    blocks_key = f"chat_blocks_{collab_id}"
    if blocks_key in st.session_state:
        del st.session_state[blocks_key]

    init_profile_form_state(current_collab)


def init_profile_form_state(current_collab: dict[str, Any]) -> None:
    """Initialize editable profile fields when switching collaboration records."""
    collab_id = current_collab["id"]
    if st.session_state.get("dashboard_profile_collab_id") == collab_id:
        return

    st.session_state.dashboard_profile_collab_id = collab_id
    st.session_state[f"edit_name_{collab_id}"] = current_collab.get("influencer_name") or ""
    st.session_state[f"edit_email_{collab_id}"] = current_collab.get("influencer_email") or ""
    st.session_state[f"edit_address_{collab_id}"] = (
        current_collab.get("influencer_shipping_address") or ""
    )
    st.session_state[f"edit_phone_{collab_id}"] = current_collab.get("influencer_phone") or ""
    st.session_state[f"edit_social_{collab_id}"] = (
        current_collab.get("influencer_social_links") or ""
    )
    st.session_state[f"edit_tags_{collab_id}"] = current_collab.get("influencer_tags") or ""


def format_dashboard_row_label(display_df: pd.DataFrame, row_index: int) -> str:
    """Build a human-readable label for dashboard row selection."""
    row = display_df.iloc[row_index]
    return (
        f"{row['资料状态']} · {row['红人姓名']} · "
        f"{row['合作产品']} · {row['合作状态']}"
    )


def render_dashboard_tab() -> None:
    """Collaboration status board with row-linked detail panel."""
    st.subheader("👥 红人资产与状态看板")
    st.caption("浏览上方总览表格，并在下方选择记录，详情区将自动联动展示。")

    db.cleanup_orphan_influencers()
    collaborations = db.get_all_collaborations()

    if not collaborations:
        st.info("暂无合作记录。请在 AI 工作台提取红人信息后会自动创建。")
        return

    raw_data_list = [dict(row) for row in collaborations]
    display_df = build_dashboard_display_df(raw_data_list)

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    if not display_df.empty:
        search_kw = st.text_input(
            "🔍 搜索红人姓名",
            placeholder="粘贴或输入红人名字进行快速过滤...",
            key="dashboard_search_kw",
        )

        all_indices = list(range(len(raw_data_list)))
        if search_kw.strip():
            filtered_indices = [
                i for i in all_indices
                if search_kw.strip().lower()
                in str(raw_data_list[i].get("influencer_name", "")).lower()
            ]
        else:
            filtered_indices = all_indices

        if not filtered_indices:
            st.info("⚠️ 没有找到匹配的红人记录，请检查拼写或尝试其他关键词。")
        else:
            current_id = st.session_state.get("selected_collab_id")
            default_index = 0
            for idx, raw_idx in enumerate(filtered_indices):
                if raw_data_list[raw_idx]["id"] == current_id:
                    default_index = idx
                    break

            selected_filtered_idx = st.selectbox(
                "📌 选择要查看的红人记录",
                options=filtered_indices,
                format_func=lambda i: format_dashboard_row_label(display_df, i),
                index=default_index,
                key="dashboard_collab_selector",
            )

            selected_collab = raw_data_list[selected_filtered_idx]

            if st.session_state.get("selected_collab_id") != selected_collab["id"]:
                st.session_state.dashboard_detail_collab_id = None
            st.session_state.selected_collab = selected_collab
            st.session_state.selected_collab_id = selected_collab["id"]

            selected_avatar_blob = get_collab_avatar_blob(selected_collab)
            if selected_avatar_blob:
                st.markdown("**🖼️ 选中红人头像预览**")
                st.image(
                    selected_avatar_blob,
                    width=100,
                    caption=selected_collab.get("influencer_name") or "红人头像",
                )

    sync_selected_collab(raw_data_list)

    if not display_df.empty:
        csv_data = display_df.to_csv(index=False).encode("utf-8-sig")
        _, download_col = st.columns([4, 1])
        with download_col:
            st.download_button(
                label="📥 一键下载完整红人资产表 (CSV)",
                data=csv_data,
                file_name="Influencer_Assets_Full.csv",
                mime="text/csv",
                use_container_width=True,
            )

    st.divider()

    if st.session_state.get("selected_collab"):
        current_collab = st.session_state.selected_collab
        collab_id = current_collab["id"]
        init_collab_detail_state(current_collab)

        st.subheader(
            f"👤 当前正在查看: {current_collab['influencer_name']} 的档案"
        )
        st.caption(
            f"合作产品: {current_collab.get('product_name', '—')} · "
            f"状态: {current_collab.get('status', '—')}"
        )

        st.markdown("#### 📝 合作与红人综合资料卡")
        st.caption("在此统一修改合作进度、物流信息以及红人的所有基础资料。")

        detail_avatar_blob = get_collab_avatar_blob(current_collab)
        if detail_avatar_blob:
            st.image(detail_avatar_blob, width=100, caption="红人头像")
        else:
            st.info("暂无头像")

        extract_col, _ = st.columns([1, 2])
        with extract_col:
            if st.button(
                "🪄 从全局聊天记录智能提取补全",
                key=f"extract_full_{collab_id}",
                use_container_width=True,
            ):
                agent = get_ai_agent()
                if agent is None:
                    st.error("请先在侧边栏配置 API Key。")
                else:
                    with st.spinner("正在呼叫 AI 深度阅读历史聊天记录，挖掘隐藏线索..."):
                        try:
                            full_history = current_collab.get("chat_history") or ""
                            if not full_history.strip():
                                st.warning("⚠️ 当前没有聊天记录可供提取！")
                            else:
                                extracted = agent.extract_influencer_info(full_history)
                                st.session_state[f"edit_name_{collab_id}"] = (
                                    extracted.get("name")
                                    or current_collab.get("influencer_name", "")
                                )
                                st.session_state[f"edit_email_{collab_id}"] = (
                                    extracted.get("email")
                                    or current_collab.get("influencer_email", "")
                                )
                                st.session_state[f"edit_address_{collab_id}"] = (
                                    extracted.get("shipping_address")
                                    or current_collab.get("influencer_shipping_address", "")
                                )
                                st.session_state[f"edit_phone_{collab_id}"] = (
                                    extracted.get("phone")
                                    or current_collab.get("influencer_phone", "")
                                )
                                st.session_state[f"edit_social_{collab_id}"] = (
                                    extracted.get("social_links")
                                    or current_collab.get("influencer_social_links", "")
                                )
                                st.success(
                                    "✅ AI 已从历史记录中挖掘出最新线索，"
                                    "请核对后点击下方保存！"
                                )
                                st.rerun()
                        except Exception as exc:
                            st.error(f"❌ 提取失败: {exc}")

        with st.form(f"unified_edit_form_{collab_id}"):
            st.markdown("**📦 合作与物流信息**")
            col_c1, col_c2, col_c3 = st.columns(3)
            with col_c1:
                new_status = st.selectbox(
                    "合作状态",
                    options=sorted(VALID_COLLAB_STATUSES),
                    index=sorted(VALID_COLLAB_STATUSES).index(current_collab["status"])
                    if current_collab["status"] in VALID_COLLAB_STATUSES
                    else 0,
                )
            with col_c2:
                new_tracking = st.text_input(
                    "追踪单号",
                    value=current_collab.get("tracking_number") or "",
                    placeholder="1Z999AA10123456784",
                )
            with col_c3:
                new_assignee = st.text_input(
                    "负责人",
                    value=current_collab.get("assigned_to") or "",
                    placeholder="运营A",
                )

            new_order_number = st.text_input(
                "订单号",
                value=current_collab.get("order_number") or "",
                placeholder="Amazon Order # / 内部样品单号",
            )

            new_product_name = st.text_input(
                "产品名称 (修改此项会同步更新产品库中的简称)",
                value=current_collab.get("product_name") or "",
                placeholder="例如：支架 / 睡袋",
            )

            st.markdown("**👤 红人基础资料**")
            profile_col1, profile_col2 = st.columns(2)
            with profile_col1:
                st.file_uploader(
                    "上传红人头像",
                    type=["jpg", "png", "jpeg"],
                    key=f"avatar_upload_{collab_id}",
                )
                st.text_input("姓名", key=f"edit_name_{collab_id}")
                st.text_input("邮箱", key=f"edit_email_{collab_id}")
                st.text_input("联系电话", key=f"edit_phone_{collab_id}")
                st.text_input("标签 (逗号分隔)", key=f"edit_tags_{collab_id}")
            with profile_col2:
                st.text_area("收货地址", height=100, key=f"edit_address_{collab_id}")
                st.text_area(
                    "社媒链接 (JSON)",
                    height=100,
                    placeholder='{"youtube": "https://...", "tiktok": "..."}',
                    key=f"edit_social_{collab_id}",
                )

            if st.form_submit_button("💾 保存所有修改", use_container_width=True, type="primary"):
                with st.spinner("正在将全部最新资料同步至全局数据库..."):
                    try:
                        edit_name = st.session_state.get(f"edit_name_{collab_id}", "").strip()
                        edit_email = st.session_state.get(f"edit_email_{collab_id}", "").strip()
                        edit_address = st.session_state.get(f"edit_address_{collab_id}", "").strip()
                        edit_phone = st.session_state.get(f"edit_phone_{collab_id}", "").strip()
                        edit_social = st.session_state.get(f"edit_social_{collab_id}", "").strip()
                        edit_tags = st.session_state.get(f"edit_tags_{collab_id}", "").strip()
                        uploaded_avatar = st.session_state.get(f"avatar_upload_{collab_id}")
                        avatar_bytes: bytes | None = None
                        if uploaded_avatar is not None:
                            avatar_bytes = uploaded_avatar.getvalue()

                        if not edit_name:
                            st.warning("⚠️ 红人姓名不能为空。")
                        else:
                            db.update_collaboration_status(
                                collaboration_id=collab_id,
                                status=new_status,
                                tracking_number=new_tracking.strip() or None,
                                order_number=new_order_number.strip() or None,
                                assigned_to=new_assignee.strip() or None,
                                last_interaction_date=datetime.now(),
                            )

                            if (
                                new_product_name.strip()
                                and new_product_name.strip() != current_collab.get("product_name")
                            ):
                                db.update_product_alias(
                                    product_id=current_collab["product_id"],
                                    new_name=new_product_name.strip(),
                                )

                            db.upsert_influencer(
                                name=edit_name,
                                email=edit_email or None,
                                social_links=edit_social or None,
                                shipping_address=edit_address or None,
                                phone=edit_phone or None,
                                tags=edit_tags or None,
                                avatar_blob=avatar_bytes,
                                influencer_id=current_collab["influencer_id"],
                            )

                            st.session_state.dashboard_detail_collab_id = None
                            st.session_state.dashboard_profile_collab_id = None
                            st.success("✅ 所有档案信息已同步更新！界面即将刷新...")
                            st.rerun()
                    except Exception as exc:
                        st.error(f"❌ 同步失败: {exc}")

        if st.button("🗑️ 永久删除该合作记录", use_container_width=True):
            try:
                if getattr(db, "delete_collaboration", None) and db.delete_collaboration(collab_id):
                    st.session_state.selected_collab = None
                    st.session_state.selected_collab_id = None
                    st.session_state.dashboard_detail_collab_id = None
                    st.success("🗑️ 记录已彻底删除，即将刷新看板...")
                    st.rerun()
                else:
                    st.error("删除失败，可能该记录已被移除或尚未支持此底层方法。")
            except Exception as exc:
                st.error(f"删除过程发生错误：{exc}")

        st.divider()
        st.markdown("#### 💬 沟通时间线与聊天记录")
        st.caption(
            "您可以随时在这里手动删减重复的测试内容、修改错误信息，"
            "保持 AI 上下文的纯净。"
        )

        blocks_key = f"chat_blocks_{collab_id}"
        if blocks_key not in st.session_state:
            st.session_state[blocks_key] = parse_chat_history_to_blocks(
                current_collab.get("chat_history") or ""
            )

        sync_chat_blocks_from_widgets(st.session_state[blocks_key])
        current_history_str = build_history_string_from_blocks(
            st.session_state[blocks_key]
        )

        st.markdown("**👁️ 预览展示区（AI 实际读取的格式）**")
        st.text_area(
            "预览区域",
            value=current_history_str,
            height=200,
            disabled=True,
            label_visibility="collapsed",
        )

        st.markdown("**⚙️ 结构化编辑器**")
        blocks_to_remove: list[str] = []
        for i, block in enumerate(st.session_state[blocks_key]):
            bid = block["id"]

            expand_key = f"expand_{bid}"
            if expand_key not in st.session_state:
                st.session_state[expand_key] = True

            with st.container():
                c1, c2, c3 = st.columns([5, 2, 2])

                with c1:
                    block["role"] = st.selectbox(
                        "发送方角色",
                        ["红人来信 Influencer", "品牌回复 Brand"],
                        index=0 if "红人" in block["role"] else 1,
                        key=f"role_{bid}",
                        label_visibility="collapsed",
                    )

                with c2:
                    is_expanded = st.session_state[expand_key]
                    btn_label = "🔼 收起内容" if is_expanded else "🔽 展开内容"
                    if st.button(btn_label, key=f"btn_toggle_{bid}", use_container_width=True):
                        st.session_state[expand_key] = not is_expanded
                        st.rerun()

                with c3:
                    if st.button("🗑️ 删除本条", key=f"del_{bid}", use_container_width=True):
                        blocks_to_remove.append(bid)

                if st.session_state[expand_key]:
                    block["content"] = st.text_area(
                        "聊天内容",
                        value=block["content"],
                        height=120,
                        key=f"content_{bid}",
                        label_visibility="collapsed",
                    )
                else:
                    st.caption(f"内容已折叠 (当前字数: {len(block['content'])})")

                st.markdown("---")

        if blocks_to_remove:
            for removed_id in blocks_to_remove:
                clear_chat_block_widget_keys(removed_id)
            st.session_state[blocks_key] = [
                block
                for block in st.session_state[blocks_key]
                if block["id"] not in blocks_to_remove
            ]
            st.rerun()

        if st.button("➕ 添加对话记录", use_container_width=True):
            st.session_state[blocks_key].append({
                "id": str(uuid.uuid4()),
                "role": "红人来信 Influencer",
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "content": "",
            })
            st.rerun()

        if st.button(
            "💾 保存聊天记录修改",
            key=f"save_history_{collab_id}",
            use_container_width=True,
            type="primary",
        ):
            with st.spinner("正在重组格式并更新档案..."):
                try:
                    sync_chat_blocks_from_widgets(st.session_state[blocks_key])
                    final_history_str = build_history_string_from_blocks(
                        st.session_state[blocks_key]
                    )

                    updated = db.update_collaboration_status(
                        collaboration_id=collab_id,
                        status=current_collab["status"],
                        chat_history=final_history_str,
                        last_interaction_date=datetime.now(),
                    )
                    if updated:
                        st.session_state.timeline_chat_history = final_history_str
                        st.session_state.dashboard_detail_collab_id = None
                        st.success("✅ 结构化聊天记录已成功更新！")
                        st.rerun()
                    else:
                        st.error("❌ 更新失败：未找到对应合作记录。")
                except Exception as exc:
                    st.error(f"❌ 更新失败: {exc}")
    else:
        st.info("请在上方表格浏览数据，并在下拉框中选择一条记录，查看详细信息与互动轨迹。")


def main() -> None:
    """Application entry point."""
    render_sidebar()

    st.markdown('<p class="crm-header">🤝 Amazon Influencer CRM</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="crm-subtitle">红人私域运营控制台 · 产品预置 · AI 沟通 · 合作追踪</p>',
        unsafe_allow_html=True,
    )

    products = db.get_all_products()
    collaborations = db.get_all_collaborations()
    m1, m2, m3 = st.columns(3)
    m1.metric("产品数", len(products))
    m2.metric("合作记录", len(collaborations))
    pending = sum(1 for c in collaborations if c["status"] == COLLAB_STATUS_PENDING)
    m3.metric("待沟通", pending)

    tab_products, tab_ai, tab_dashboard = st.tabs(
        ["📁 产品库管理", "🤖 AI 沟通工作台", "👥 红人资产与状态看板"]
    )

    with tab_products:
        render_products_tab()
    with tab_ai:
        render_ai_workbench_tab()
    with tab_dashboard:
        render_dashboard_tab()


if __name__ == "__main__":
    main()
