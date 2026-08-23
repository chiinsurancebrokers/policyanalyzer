"""
Insurance Policy Analyzer — full pipeline.

Upload IPMI/health policy documents (PDF/TXT) -> structured extraction per
provider (excess, underwriting, area of cover, clause-level critical
limitations) -> side-by-side comparison -> capture the applicant's stated
needs -> HAL (Claude) recommends the best-fit plan with reasoning ->
optional GPT-4o-mini second opinion on HAL's recommendation -> export to
Excel or a client-ready PDF report.
"""
import logging
import tempfile
from pathlib import Path

import streamlit as st

from core.ai_analyzer import analyze_with_ai
from core.comparison import build_dataframe, to_excel_bytes, to_markdown
from core.config import config
from core.extract import extract_text
from core.gpt_verifier import verify_recommendation_with_gpt, verify_with_gpt
from core.grouping import GroupDoc, combine_documents, combined_hash
from core.pdf_report import build_pdf_bytes
from core.recommender import recommend_plan

logging.basicConfig(level=logging.INFO)

st.set_page_config(page_title="Policy Analyzer", page_icon="📋", layout="wide")

PRIORITY_OPTIONS = [
    "Low/no excess", "High annual limit", "Outpatient cover", "Inpatient cover",
    "Maternity", "Dental", "Mental health", "Chronic condition management",
    "Emergency evacuation", "Worldwide cover incl. USA", "Low premium / budget",
]


def applicant_needs_form():
    st.subheader("👤 Applicant needs")
    st.caption("This drives HAL's recommendation below — the more specific, the better the fit.")
    col1, col2 = st.columns(2)
    with col1:
        age_range = st.text_input("Age(s) / dependents", placeholder="e.g. 42 (self), 39 (spouse), 8 & 11 (children)")
        residence_country = st.text_input("Country of residence", placeholder="e.g. Greece")
        travel_area = st.text_input("Area of cover needed", placeholder="e.g. Worldwide incl. USA, or Europe only")
    with col2:
        budget_annual = st.text_input("Annual budget", placeholder="e.g. up to EUR 4,000")
        known_conditions = st.text_area("Known / pre-existing conditions", placeholder="e.g. none, or specify", height=68)
    priorities = st.multiselect("Stated priorities", PRIORITY_OPTIONS)
    notes = st.text_area("Additional notes for HAL", placeholder="Anything else relevant to the recommendation", height=68)

    return {
        "age_range": age_range.strip(),
        "residence_country": residence_country.strip(),
        "travel_area": travel_area.strip(),
        "budget_annual": budget_annual.strip(),
        "known_conditions": known_conditions.strip(),
        "priorities": priorities,
        "notes": notes.strip(),
    }


def render_hal_recommendation(hal: dict):
    if "error" in hal:
        st.warning(f"HAL recommendation unavailable: {hal['error']}")
        return

    st.markdown(f"### 🏆 Recommended: {hal.get('recommended_provider', '—')}")
    st.write(hal.get("reasoning", ""))

    ranking = hal.get("ranking", [])
    if ranking:
        st.markdown("**Ranking**")
        for r in ranking:
            st.markdown(f"- **{r.get('provider', '')}** — {r.get('fit_summary', '')}")

    tradeoffs = hal.get("key_tradeoffs", [])
    if tradeoffs:
        st.markdown("**Key trade-offs**")
        for t in tradeoffs:
            st.markdown(f"- {t}")

    caveats = hal.get("flagged_caveats", [])
    if caveats:
        st.warning("**⚠️ Caveats flagged for this client:**\n\n" + "\n".join(f"- {c}" for c in caveats))

    st.caption(f"HAL confidence: {hal.get('confidence', 'unknown')} · {hal.get('method', '')}")


def render_second_opinion(verification: dict):
    if "error" in verification:
        st.warning(f"Second opinion unavailable: {verification['error']}")
        return

    agreement = verification.get("agreement", "unknown")
    icon = {"agree": "✅", "partial": "⚠️", "disagree": "🔴"}.get(agreement, "ℹ️")
    st.markdown(f"**{icon} ChatGPT verdict: {agreement}**")
    if verification.get("alternative_provider"):
        st.markdown(f"**Alternative suggested:** {verification['alternative_provider']}")
    st.write(verification.get("reasoning", ""))

    concerns = verification.get("concerns", [])
    if concerns:
        st.markdown("**Concerns raised:**")
        for c in concerns:
            st.markdown(f"- {c}")

    if verification.get("notes"):
        st.caption(verification["notes"])


def main():
    st.title("📋 Insurance Policy Analyzer")
    st.caption("Upload IPMI/health policy documents, compare coverage, and get HAL's recommendation for your client.")

    if not config.has_ai:
        st.warning(
            "No ANTHROPIC_API_KEY configured — running in rule-based fallback mode only. "
            "Set the key in your environment / Railway variables for full AI extraction and recommendations.",
            icon="⚠️",
        )

    st.session_state.setdefault("n_providers", 2)

    st.subheader("Policies to compare")
    st.caption(
        "For each provider, upload its quotation/certificate, its policy wording, and any "
        "supporting documents. They're merged and analyzed as ONE policy — the quotation's "
        "specific figures take priority, the wording fills in exclusions, underwriting, and "
        "limitations. Add as many providers as you're comparing."
    )

    provider_slots = []
    for i in range(st.session_state["n_providers"]):
        with st.container(border=True):
            label = st.text_input(f"Provider / policy label", value=f"Provider {i + 1}", key=f"provider_label_{i}")
            col1, col2, col3 = st.columns(3)
            with col1:
                quote_files = st.file_uploader(
                    "Quotation / Policy Certificate", type=["pdf", "txt"],
                    accept_multiple_files=True, key=f"quote_files_{i}",
                    help="Short, applicant-specific — selected tier, premium, sums insured.",
                )
            with col2:
                wording_files = st.file_uploader(
                    "Policy Wording / Member Guide", type=["pdf", "txt"],
                    accept_multiple_files=True, key=f"wording_files_{i}",
                    help="The full terms and conditions — exclusions, underwriting, limitations.",
                )
            with col3:
                support_files = st.file_uploader(
                    "Supporting documents (optional)", type=["pdf", "txt"],
                    accept_multiple_files=True, key=f"support_files_{i}",
                    help="Riders, endorsements, benefit tables, correspondence.",
                )
        provider_slots.append({
            "label": label.strip() or f"Provider {i + 1}",
            "quote_files": quote_files or [],
            "wording_files": wording_files or [],
            "support_files": support_files or [],
        })

    col_add, col_remove, _ = st.columns([1, 1, 3])
    with col_add:
        if st.button("➕ Add another provider"):
            st.session_state["n_providers"] += 1
            st.rerun()
    with col_remove:
        if st.session_state["n_providers"] > 1:
            if st.button("➖ Remove last provider"):
                st.session_state["n_providers"] -= 1
                st.rerun()

    active_slots = [s for s in provider_slots if s["quote_files"] or s["wording_files"] or s["support_files"]]

    if not active_slots:
        st.info("👆 Upload at least a quotation or policy wording for Provider 1 to get started.")
        with st.expander("How it works"):
            st.markdown(
                """
                1. For each provider, upload its quotation/certificate, policy wording, and any
                   supporting documents — each provider's documents are merged into one policy
                2. Each merged policy is analyzed with Claude: benefit categories, deductible,
                   excess, annual limit, area of cover, underwriting basis, waiting periods,
                   and clause-level **critical limitations** (e.g. day caps on specific
                   treatments) that a client could easily miss — the quotation's specific
                   figures take priority over generic examples in the wording
                3. Tell HAL about the applicant's needs and priorities
                4. HAL (Claude) recommends the best-fit plan, ranked, with plain-language
                   reasoning and any caveats relevant to this specific client
                5. Optionally, ask ChatGPT for a second opinion — on the extraction, or on
                   HAL's recommendation itself — one at a time, only when you ask for it
                6. Export a full client-ready PDF report, or Excel/Markdown
                7. Repeat uploads of the same documents (or same applicant needs) are served
                   from cache — no repeat cost
                """
            )
        return

    st.divider()
    applicant_profile = applicant_needs_form()
    st.session_state["applicant_profile"] = applicant_profile

    st.divider()
    if st.button("🚀 Analyze & Compare", type="primary"):
        records = []
        progress = st.progress(0.0)
        status = st.empty()

        def _extract_files(files, doc_type):
            docs = []
            for f in files:
                with tempfile.NamedTemporaryFile(delete=False, suffix=Path(f.name).suffix) as tmp:
                    tmp.write(f.getvalue())
                    tmp_path = tmp.name
                extraction = extract_text(tmp_path, f.name)
                Path(tmp_path).unlink(missing_ok=True)
                if not extraction.ok:
                    st.error(f"❌ {f.name}: {extraction.error}")
                    continue
                docs.append(GroupDoc(filename=f.name, text=extraction.text, doc_type=doc_type))
            return docs

        for i, slot in enumerate(active_slots):
            status.text(f"Processing {slot['label']}...")
            progress.progress((i + 1) / max(len(active_slots), 1))

            docs = (
                _extract_files(slot["quote_files"], "quotation")
                + _extract_files(slot["wording_files"], "wording")
                + _extract_files(slot["support_files"], "supporting")
            )
            if not docs:
                continue  # every file in this slot failed extraction

            merged_text = combine_documents(docs)
            merge_hash = combined_hash(docs)

            analysis = analyze_with_ai(merged_text, merge_hash)
            records.append({
                "provider": slot["label"],
                "text": merged_text,
                "content_hash": merge_hash,
                "analysis": analysis,
                "verification": None,
                "source_files": [d.filename for d in docs],
            })

            merge_note = f" (merged {len(docs)} documents)" if len(docs) > 1 else ""
            st.success(f"✅ {slot['label']}: analyzed via {analysis.get('method', 'unknown')}{merge_note}")

        progress.empty()
        status.empty()

        # Persist across reruns (e.g. clicking a per-provider verify button below)
        st.session_state["analysis_records"] = records
        st.session_state["hal_recommendation"] = None
        st.session_state["recommendation_second_opinion"] = None

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
            source_files = record.get("source_files")
            if source_files and len(source_files) > 1:
                st.caption(f"📎 Merged from {len(source_files)} documents: {', '.join(source_files)}")
            st.write(data.get("coverage_summary", "—"))
            if data.get("confidence") == "low":
                st.caption("⚠️ Low-confidence extraction — verify against the source document.")

            limitations = data.get("critical_limitations", [])
            if limitations:
                st.markdown("**⚠️ Critical limitations to flag with the client:**")
                for item in limitations:
                    st.markdown(f"- **{item.get('topic', '')}:** {item.get('detail', '')}")

            st.divider()
            if not config.has_gpt_verification:
                st.caption("Set OPENAI_API_KEY to enable GPT-4o-mini second-opinion verification.")
            elif record["verification"] is None:
                if st.button("🔍 Get GPT-4o-mini second opinion on this extraction", key=f"verify_{i}"):
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

    st.divider()
    st.subheader("🤖 HAL's Recommendation")
    hal_recommendation = st.session_state.get("hal_recommendation")

    if not config.has_ai:
        st.caption("Set ANTHROPIC_API_KEY to enable HAL's recommendation.")
    elif hal_recommendation is None:
        if st.button("✨ Get HAL's recommendation for this applicant", type="primary"):
            with st.spinner("HAL is comparing plans against the applicant's needs..."):
                hal_recommendation = recommend_plan(st.session_state["applicant_profile"], records)
            st.session_state["hal_recommendation"] = hal_recommendation
            st.rerun()
    else:
        render_hal_recommendation(hal_recommendation)
        if st.button("🔄 Re-run HAL's recommendation"):
            st.session_state["hal_recommendation"] = None
            st.session_state["recommendation_second_opinion"] = None
            st.rerun()

        st.divider()
        st.subheader("🔍 Second Opinion on HAL's Recommendation")
        second_opinion = st.session_state.get("recommendation_second_opinion")
        if not config.has_gpt_verification:
            st.caption("Set OPENAI_API_KEY to enable ChatGPT's second opinion on HAL's recommendation.")
        elif second_opinion is None and "error" not in hal_recommendation:
            if st.button("🔍 Get ChatGPT's second opinion on HAL's pick"):
                with st.spinner("ChatGPT independently reviewing HAL's recommendation..."):
                    second_opinion = verify_recommendation_with_gpt(
                        st.session_state["applicant_profile"], records, hal_recommendation
                    )
                st.session_state["recommendation_second_opinion"] = second_opinion
                st.rerun()
        elif second_opinion is not None:
            render_second_opinion(second_opinion)

    st.divider()
    st.subheader("💾 Export")
    col1, col2, col3 = st.columns(3)
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
    with col3:
        pdf_bytes = build_pdf_bytes(
            applicant_profile=st.session_state.get("applicant_profile", {}),
            records=records,
            hal_recommendation=st.session_state.get("hal_recommendation"),
            second_opinion=st.session_state.get("recommendation_second_opinion"),
        )
        st.download_button(
            "📄 Download PDF Report",
            pdf_bytes,
            "policy_comparison_report.pdf",
            "application/pdf",
        )


if __name__ == "__main__":
    main()
