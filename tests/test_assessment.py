from tests.test_trace import AT, _certs, _edges, _instruments, _submit


def _assess(client, ids, codes=None, at=AT):
    body = {"at": at, "deactivated_certificate_ids": ids}
    if codes is not None:
        body["instrument_codes"] = codes
    return client.post("/api/assessments/certificate-deactivation", json=body)


def _by_code(payload):
    return {r["instrument_code"]: r for r in payload["results"]}


def test_deactivate_reference_cert_degrades_chain(client):
    _submit(client)  # CW -> CR -> CG，唯一有效链

    r = _assess(client, ["CR"])
    assert r.status_code == 200, r.text
    payload = r.json()

    assert payload["simulation_only"] is True
    assert payload["filtered_by_instruments"] is False
    assert payload["deactivated_certificate_ids"] == ["CR"]
    # CR 停用后关联的两条边都被模拟移除
    removed = {(e["child_cert_id"], e["parent_cert_id"]) for e in payload["removed_edges"]}
    assert removed == {("CW", "CR"), ("CR", "CG")}

    # 受影响仪器：W（路径经过 CR）与 R（CR 所属仪器）；G 不受影响
    assert payload["affected_instrument_count"] == 2
    assert set(_by_code(payload)) == {"R", "W"}

    w = _by_code(payload)["W"]
    assert w["impact"] == "degraded"
    assert w["before"]["resolution"] == "unique"
    assert w["before"]["active_chain"]["certificate_ids"] == ["CW", "CR", "CG"]
    assert w["after"]["resolution"] == "broken"
    assert w["after"]["active_chain"] is None
    # CW 失去唯一的向上溯源边 -> 死路，且给出可解释冲突
    assert w["removed_certificates_on_paths"] == ["CR"]
    assert any(
        c["code"] == "DEAD_END" and c["node"] == "CW"
        for c in w["new_conflicts"]
    )
    assert all("deactivation_note" in c for c in w["new_conflicts"])


def test_deactivation_is_simulation_only(client):
    _submit(client)
    assert _assess(client, ["CR"]).status_code == 200

    # 评估后数据库内容不变：仍可查到 CR 与原唯一链
    assert client.get("/api/certificates/CR").status_code == 200
    report = client.get(f"/api/trace/W?at={AT}").json()
    assert report["resolution"] == "unique"
    assert [n["certificate_id"] for n in report["active_chain"]["nodes"]] == [
        "CW", "CR", "CG",
    ]
    assert len(client.get("/api/edges").json()) == 2


def test_deactivating_one_branch_resolves_ambiguity(client):
    insts = _instruments() + [{"code": "R2", "name": "第二参考标准器"}]
    certs = _certs() + [
        {"id": "CR2", "instrument_code": "R2", "cert_number": "R2-1",
         "issued_at": "2025-07-01T00:00:00",
         "valid_from": "2025-07-01T00:00:00",
         "valid_until": "2027-07-01T00:00:00",
         "uncertainty": 0.002, "unit": "mm"},
    ]
    edges = _edges() + [
        {"child_cert_id": "CW", "parent_cert_id": "CR2"},
        {"child_cert_id": "CR2", "parent_cert_id": "CG"},
    ]
    _submit(client, insts, certs, edges)

    r = _assess(client, ["CR"])
    payload = r.json()
    w = _by_code(payload)["W"]

    # 多候选 -> 剩余唯一有效链，属于改善
    assert w["impact"] == "improved"
    assert w["before"]["resolution"] == "ambiguous"
    assert w["after"]["resolution"] == "unique"
    assert w["after"]["valid"] is True
    chain = w["after"]["active_chain"]
    assert chain["certificate_ids"] == ["CW", "CR2", "CG"]
    assert chain["role"] == "active"
    assert w["new_conflicts"] == []
    assert payload["summary"]["improved"] >= 1


def test_filter_by_instrument_includes_unaffected_as_unchanged(client):
    _submit(client)

    r = _assess(client, ["CR"], codes=["W", "G"])
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["filtered_by_instruments"] is True
    assert payload["affected_instrument_count"] == 2

    results = _by_code(payload)
    assert set(results) == {"W", "G"}
    assert results["W"]["impact"] == "degraded"
    assert results["G"]["impact"] == "unchanged"
    assert results["G"]["before"]["resolution"] == "unique"
    assert results["G"]["after"]["resolution"] == "unique"


def test_deactivate_national_standard_root(client):
    _submit(client)
    payload = _assess(client, ["CG"]).json()
    results = _by_code(payload)
    # 根证书消失：CR 成为死路，W、R 均断裂
    assert results["W"]["after"]["resolution"] == "broken"
    assert results["R"]["after"]["resolution"] == "broken"
    assert results["G"]["after"]["resolution"] == "broken"
    assert results["G"]["removed_certificates_on_paths"] == ["CG"]


def test_unknown_certificate_422_and_unknown_instrument_404(client):
    _submit(client)

    r = _assess(client, ["NOPE"])
    assert r.status_code == 422
    assert "NOPE" in r.json()["detail"]

    r = _assess(client, ["CR"], codes=["W", "GHOST"])
    assert r.status_code == 404
    assert "GHOST" in r.json()["detail"]


def test_empty_certificate_list_rejected(client):
    r = client.post(
        "/api/assessments/certificate-deactivation",
        json={"at": AT, "deactivated_certificate_ids": []},
    )
    assert r.status_code == 422
