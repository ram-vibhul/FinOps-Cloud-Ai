"""Kubernetes manifests for the platform (D.5): hardened pods, no secrets in the repo."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
K8S = ROOT / "deploy" / "kubernetes"


def docs() -> list[dict]:
    out = []
    for path in sorted(K8S.glob("*.yaml")):
        out += [d for d in yaml.safe_load_all(path.read_text()) if d]
    return out


def pod_specs() -> list[tuple[str, dict]]:
    found = []
    for d in docs():
        if d.get("kind") in ("Deployment", "Job"):
            found.append((d["metadata"]["name"], d["spec"]["template"]["spec"]))
    return found


def test_kustomization_lists_every_manifest() -> None:
    kust = yaml.safe_load((K8S / "kustomization.yaml").read_text())
    files = {p.name for p in K8S.glob("*.yaml")} - {"kustomization.yaml"}
    assert set(kust["resources"]) == files
    assert kust["secretGenerator"][0]["envs"] == ["costlens.env"]
    assert all(i["newTag"] != "latest" for i in kust["images"])


def test_pods_are_hardened() -> None:
    specs = pod_specs()
    assert {name for name, _ in specs} == {"costlens-web", "costlens-worker", "costlens-migrate"}
    for name, spec in specs:
        assert spec["securityContext"]["runAsNonRoot"] is True, name
        assert spec["automountServiceAccountToken"] is False, name
        for c in spec["containers"]:
            sc = c["securityContext"]
            assert sc["readOnlyRootFilesystem"] and not sc["allowPrivilegeEscalation"], name
            assert sc["capabilities"]["drop"] == ["ALL"], name
            assert "limits" in c["resources"], name
            assert c["envFrom"][0]["secretRef"]["name"] == "costlens-env", name


def test_web_and_worker_share_the_data_volume() -> None:
    claims = {
        name: [
            v["persistentVolumeClaim"]["claimName"]
            for v in spec["volumes"]
            if "persistentVolumeClaim" in v
        ]
        for name, spec in pod_specs()
    }
    assert claims["costlens-web"] == claims["costlens-worker"] == ["costlens-data"]
    pvc = next(d for d in docs() if d["kind"] == "PersistentVolumeClaim")
    assert pvc["spec"]["accessModes"] == ["ReadWriteMany"]


def test_no_real_settings_file_is_committed() -> None:
    assert not (K8S / "costlens.env").exists()
    assert "deploy/kubernetes/costlens.env" in (ROOT / ".gitignore").read_text()
    example = (K8S / "costlens.env.example").read_text()
    assert "COSTLENS_SECRET_KEY=\n" in example and "COSTLENS_ENCRYPTION_KEYS=\n" in example
