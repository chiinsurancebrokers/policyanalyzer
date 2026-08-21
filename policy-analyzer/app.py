"""
Insurance Policy Analyzer — consolidated single-app rewrite.

Upload IPMI/health policy documents (PDF/TXT), get a structured
side-by-side comparison, export to Excel.
"""
import logging
import tempfile
from pathlib import Path

import streamlit as st

from core.ai_analyzer import analyze_with_ai
from core.comparison import build_dataframe, to_excel_bytes, to_markdown
from core.config import config
from core.extract import extract_text, infer_provider_name
from core.gpt_verifier import verify_with_gpt

logging.basicConfig(level=logging.INFO)

st.set_page_config(page_title="Policy Analyzer", page_icon="📋", layout="wide")


def main():
    st.title("📋 Insurance Policy Analyzer")
    st.caption("Upload IPMI/health policy documents to compare coverage side by side.")

    if not config.has_ai:
        st.warning(
            "No ANTHROPIC_API_KEY configured — running in rule-based fallback mode only. "
            "Set the key in your environment / Railway variables for full AI extraction.",
            icon="⚠️",
        )

    uploaded_files = st.file_uploader(
        "Upload policy documents (PDF or TXT)",
        type=["pdf", "txt"],
        accept_multiple_files=True,
        help=f"Up to {config.MAX_FILE_SIZE_MB}MB per file",
    )

    if not uploaded_files:
        st.info("👆 Upload one or more policy documents to get started.")
        with st.expander("How it works"):
            st.markdown(
                """
                1. Upload policy wordings (PDF or TXT)
                2. Provider names are auto-detected — adjust if needed
                3. Each document is analyzed with Claude (structured extraction of
                   coverage categories, deductibles, limits, exclusions)
                4. Compare side by side and export to Excel
                5. Optionally, ask GPT-4o-mini to review Claude's extraction against
                   the source text as a second opinion — flags disagreements, one
                   document at a time, only when you ask for it
                6. Repeat uploads of the same document are served from cache — no repeat cost
                """
            )
        return

    st.subheader("Provider names")
    providers = []
    for i, file in enumerate(uploaded_files):
        guess = infer_provider_name("", file.name)
        col1, col2 = st.columns([2, 1])
        with col1:
            name = st.text_input(f"Provider for {file.name}", value=guess, key=f"provider_{i}")
        with col2:
            st.caption(f"📄 {file.name}")
        providers.append(name.strip() or f"Provider {i + 1}")

    if st.button("🚀 Analyze & Compare", type="primary"):
        records = []
        progress = st.progress(0.0)
        status = st.empty()

        for i, (file, provider) in enumerate(zip(uploaded_files, providers)):
            status.text(f"Processing {provider}...")
            progress.progress((i + 1) / len(uploaded_files))

            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.name).suffix) as tmp:
                tmp.write(file.getvalue())
                tmp_path = tmp.name

            extraction = extract_text(tmp_path, file.name)
            Path(tmp_path).unlink(missing_ok=True)

            if not extraction.ok:
                st.error(f"❌ {provider}: {extraction.error}")
                continue

            # Re-check provider name against document text now that we have it
            confirmed_guess = infer_provider_name(extraction.text, file.name)
            if confirmed_guess != "Unknown":
                provider = provider if provider != f"Provider {i + 1}" else confirmed_guess

            analysis = analyze_with_ai(extraction.text, extraction.content_hash)
            records.append({
                "provider": provider,
                "text": extraction.text,
                "content_hash": extraction.content_hash,
                "analysis": analysis,
                "verification": None,
            })
            st.success(f"✅ {provider}: analyzed via {analysis.get('method', 'unknown')}")

        progress.empty()
        status.empty()

        # Persist across reruns (e.g. clicking a per-provider verify button below)
        st.session_state["analysis_records"] = records

    records = st.session_state.get("analysis_records")
    if not records:
        return

    st.subheader("📊 Comparison")
    results_for_df = [(r["provider"], r["analysis"]) for r in records]
    df = build_dataframe(results_for_df)
    st.dataframe(df, use_container_width=True)

    for i, record in enumerate(records):
        provider, data = record["provider"], record["analysis"]
        with st.expander(f"📝 {provider} — full summary"):
            st.write(data.get("coverage_summary", "—"))
            if data.get("confidence") == "low":
                st.caption("⚠️ Low-confidence extraction — verify against the source document.")

            st.divider()
            if not config.has_gpt_verification:
                st.caption("Set OPENAI_API_KEY to enable GPT-4o-mini second-opinion verification.")
            elif record["verification"] is None:
                if st.button("🔍 Get GPT-4o-mini second opinion", key=f"verify_{i}"):
                    with st.spinner("GPT-4o-mini reviewing Claude's extraction..."):
                        verification = verify_with_gpt(record["text"], data, record["content_hash"])
                    st.session_state["analysis_records"][i]["verification"] = verification
                    st.rerun()
            else:
                verification = record["verification"]
                if "error" in verification:
                    st.warning(f"Verification unavailable: {verification['error']}")
                else:
                    agreement = verification.get("agreement", "unknown")
                    icon = {"agree": "✅", "partial": "⚠️", "disagree": "🔴"}.get(agreement, "ℹ️")
                    st.markdown(f"**{icon} GPT-4o-mini verdict: {agreement}**")
                    st.write(verification.get("notes", ""))
                    if verification.get("discrepancies"):
                        st.markdown("**Discrepancies flagged:**")
                        for d in verification["discrepancies"]:
                            st.markdown(f"- {d}")
                    if verification.get("missing"):
                        st.markdown("**Possibly missed:**")
                        for m in verification["missing"]:
                            st.markdown(f"- {m}")

    st.subheader("💾 Export")
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "📊 Download Excel",
            to_excel_bytes(df),
            "policy_comparison.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with col2:
        with st.popover("📄 View Markdown"):
            st.code(to_markdown(df), language="markdown")


if __name__ == "__main__":
    main()
