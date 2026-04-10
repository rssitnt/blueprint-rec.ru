from __future__ import annotations

from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.schemas import CalloutCandidate, CandidateKind, CandidateReviewStatus
from app.services import leader_topology as leader_topology_module
from app.services.leader_topology import LeaderTopologyAnalyzer


def test_analyze_returns_empty_when_cv2_raises(monkeypatch):
    if leader_topology_module.cv2 is None or leader_topology_module.np is None:
        return

    analyzer = LeaderTopologyAnalyzer()
    candidate = CalloutCandidate(
        kind=CandidateKind.CIRCLE,
        center_x=50,
        center_y=50,
        bbox_x=40,
        bbox_y=40,
        bbox_width=20,
        bbox_height=20,
        score=0.9,
        crop_url="",
        review_status=CandidateReviewStatus.PENDING,
    )

    monkeypatch.setattr(leader_topology_module.cv2, "HoughLinesP", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    observations = analyzer.analyze(Image.new("RGB", (120, 120), color="white"), [candidate])

    assert observations == {}
