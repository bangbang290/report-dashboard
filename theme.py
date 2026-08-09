"""
theme.py
관세청 내부 인터페이스 톤(아이보리 배경 + 남색/파란색 포인트) + 마타 캐릭터를
화면 곳곳에 자연스럽게 쓰기 위한 공통 헬퍼.
색상 자체는 .streamlit/config.toml 에서 관리하고, 여기서는 마타 이미지 경로와
버튼/탭 모양 등 약간의 커스텀 CSS만 다룹니다.
"""

import os
import streamlit as st

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "mata")

MATA = {
    "official": os.path.join(ASSETS_DIR, "mata_official.png"),   # 배지 든 공식 포즈 - 로그인/브랜딩용
    "standard": os.path.join(ASSETS_DIR, "mata_standard.png"),   # 기본 인사 포즈 - 헤더용
    "thumbsup": os.path.join(ASSETS_DIR, "mata_thumbsup.png"),   # 엄지척 - 환영/완료 메시지용
    "back": os.path.join(ASSETS_DIR, "mata_back.png"),           # 뒷모습 - 푸터용
    "thinking": os.path.join(ASSETS_DIR, "mata_thinking.png"),   # 생각하는 포즈 - 빈 상태(empty state)용
}


def inject_custom_css():
    """버튼을 알약(pill) 모양으로, 탭 강조색을 넥타이 파란색으로 - 관세e음 느낌에 맞춰 살짝만 손봄."""
    st.markdown(
        """
        <style>
        /* 버튼: 관세e음 상단 메뉴처럼 알약 모양 */
        .stButton > button {
            border-radius: 999px !important;
        }
        /* 탭 선택 강조색 */
        .stTabs [aria-selected="true"] {
            color: #005da7 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def show_mata(pose: str, width: int = 120, caption: str = None):
    """지정한 포즈의 마타 이미지를 보여줌. pose는 MATA 딕셔너리의 키."""
    path = MATA.get(pose)
    if path and os.path.exists(path):
        st.image(path, width=width, caption=caption)
