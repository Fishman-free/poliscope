from __future__ import annotations


def load_case_inventory() -> list[dict[str, object]]:
    return [
        {
            "case_id": "screen_time_mh_2024",
            "question": "Does screen time affect adolescent mental health?",
            "expected_blindspots": ("confounding", "measurement_bias"),
            "closed_corpus_date": "2024-01-01",
        },
        {
            "case_id": "social_media_sleep_2023",
            "question": "Does social media use disrupt sleep?",
            "expected_blindspots": ("reverse_causation", "self_report_bias"),
            "closed_corpus_date": "2023-06-01",
        },
    ]
