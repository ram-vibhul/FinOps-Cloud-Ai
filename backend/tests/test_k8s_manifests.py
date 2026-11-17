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
    assert kust["secretGenerator"][0]["envs"] == ["ccradar.env"]
    assert all(i["newTag"] != "latest" for i in kust["images"])


def test_pods_are_hardened() -> None:
    specs = pod_specs()
    assert {name for name, _ in specs} == {"ccradar-web", "ccradar-worker", "ccradar-migrate"}
    for name, spec in specs:
        assert spec["securityContext"]["runAsNonRoot"] is True, name
        assert spec["automountServiceAccountToken"] is False, name
        for c in spec["containers"]:
            sc = c["securityContext"]
            assert sc["readOnlyRootFilesystem"] and not sc["allowPrivilegeEscalation"], name
            assert sc["capabilities"]["drop"] == ["ALL"], name
            assert "limits" in c["resources"], name
            assert c["envFrom"][0]["secretRef"]["name"] == "ccradar-env", name


def test_web_and_worker_share_the_data_volume() -> None:
    claims = {
        name: [
            v["persistentVolumeClaim"]["claimName"]
            for v in spec["volumes"]
            if "persistentVolumeClaim" in v
        ]
        for name, spec in pod_specs()
    }
    assert claims["ccradar-web"] == claims["ccradar-worker"] == ["ccradar-data"]
    pvc = next(d for d in docs() if d["kind"] == "PersistentVolumeClaim")
    assert pvc["spec"]["accessModes"] == ["ReadWriteMany"]


def test_no_real_settings_file_is_committed() -> None:
    assert not (K8S / "ccradar.env").exists()
    assert "deploy/kubernetes/ccradar.env" in (ROOT / ".gitignore").read_text()
    example = (K8S / "ccradar.env.example").read_text()
    assert "CCRADAR_SECRET_KEY=\n" in example and "CCRADAR_ENCRYPTION_KEYS=\n" in example


def test_image_keeps_data_in_the_volume() -> None:
    # Without it the app wrote to ./data under /app, which the non-root user cannot do,
    # and the container crashed at start (found running deploy/compose/local.yaml).
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "CCRADAR_DATA_DIR=/data" in dockerfile and 'VOLUME ["/data"]' in dockerfile


def test_forwarded_headers_are_not_trusted_from_anyone() -> None:
    # 11.3: with "*", any pod could fake its client IP and get past the rate limits.
    web = next(
        d
        for d in docs()
        if d.get("kind") == "Deployment" and d["metadata"]["name"] == "ccradar-web"
    )
    env = {e["name"]: e["value"] for e in web["spec"]["template"]["spec"]["containers"][0]["env"]}
    trusted = env["CCRADAR_FORWARDED_ALLOW_IPS"]
    assert "*" not in trusted and trusted
    import ipaddress

    for net in trusted.split(","):
        assert ipaddress.ip_network(net).is_private, net
