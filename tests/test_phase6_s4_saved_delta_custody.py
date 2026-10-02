from pathlib import Path

import pytest

from tests.fixtures.phase6_s4_saved_delta import SavedDeltaInputError, build_final_freeze, canonical_sha, finalize
import hashlib

from tests.fixtures.phase6_s4_saved_delta_custody import ARTIFACT_ORDER, freeze_container, validate_artifact_set, write_once
from tests.test_phase6_s4_saved_delta import D, _sealed, _source


def test_write_once_preserves_source_and_rejects_overwrite(tmp_path: Path):
    source={"root":"fixed"}; payload={"status":"COMPLETE","counts":{"UNKNOWN":2}}
    destination=tmp_path/"private"/"aggregate.json"
    evidence=write_once(root=tmp_path,destination=destination,payload=payload,
                        expected_source_sha256=canonical_sha(source),source_payload=source)
    assert evidence["artifact_sha256"]==canonical_sha(payload)
    with pytest.raises(SavedDeltaInputError):
        write_once(root=tmp_path,destination=destination,payload=payload,
                   expected_source_sha256=canonical_sha(source),source_payload=source)


def test_write_once_rejects_source_change_and_escape(tmp_path: Path):
    with pytest.raises(SavedDeltaInputError):
        write_once(root=tmp_path,destination=tmp_path/"x.json",payload={},expected_source_sha256="a"*64,source_payload={})
    with pytest.raises(SavedDeltaInputError):
        write_once(root=tmp_path,destination=tmp_path.parent/"escape.json",payload={},expected_source_sha256=canonical_sha({}),source_payload={})


def _artifacts():
    source,runtime,old_final,old_annotations=_source(); pre,candidate,pin,sealed,frozen,rubric,_=_sealed(source,runtime)
    completed=finalize(source_manifest=source,runtime_rows=runtime,finalized_artifact=old_final,annotation_artifact=old_annotations,preflight=pre,candidate_manifest=candidate,main_pin=pin,sealed=sealed,frozen_annotations=frozen,expected_pin_sha256=pin["pin_sha256"],repository_root=Path.cwd(),source_root=Path("tests/fixtures"),expected_t560_bundle_sha256=source["t560_approved_dependency_bundle"]["bundle_sha256"],expected_t562_bundle_sha256=source["t562_approved_dependency_bundle"]["bundle_sha256"])
    aggregate=completed["aggregate"]
    final=build_final_freeze(source_manifest=source,preflight=pre,candidate_manifest=candidate,sealed=sealed,frozen_annotations=frozen,aggregate=aggregate,expected_pin_sha256=pin["pin_sha256"],mapping_sha256=canonical_sha(sealed["mapping"]),helper_source_sha256=D,repository_root=Path.cwd(),source_root=Path("tests/fixtures"),expected_t560_bundle_sha256=source["t560_approved_dependency_bundle"]["bundle_sha256"],expected_t562_bundle_sha256=source["t562_approved_dependency_bundle"]["bundle_sha256"])
    artifacts={"source-manifest.json":source,"preflight.json":pre,"candidate-manifest.json":candidate,"main-pin.json":pin,"mapping.json":sealed["mapping"],"blind-packet.json":sealed["packet"],"rubric.json":rubric,"packet-freeze.json":sealed["packet_freeze"],"annotations.json":frozen["annotations"],"annotation-freeze.json":frozen["annotation_freeze"],"aggregate.json":aggregate,"final-freeze.json":final["final_freeze"],"safe-summary.json":final["safe_summary"]}
    return artifacts,completed["decision_wrappers"]


def _portable_code(tmp_path: Path) -> tuple[Path, str]:
    code=tmp_path/"code"/"phase6_s4_saved_delta.py"
    code.parent.mkdir()
    code.write_bytes(Path("tests/fixtures/phase6_s4_saved_delta.py").read_bytes())
    return code,hashlib.sha256(code.read_bytes()).hexdigest()


def test_full_container_is_create_only_and_binds_begin_end_source_code(tmp_path: Path):
    source={"manifest":"fixed"}; code,code_sha=_portable_code(tmp_path)
    artifacts,wrappers=_artifacts()
    evidence=freeze_container(root=tmp_path,output_dir=tmp_path/"container",artifacts=artifacts,source_payload=source,
                              expected_source_sha256=canonical_sha(source),code_files=(code,),expected_code_sha256=(code_sha,),decision_wrappers=wrappers)
    assert evidence["status"]=="COMPLETE" and tuple(evidence["artifacts"])==ARTIFACT_ORDER
    with pytest.raises(SavedDeltaInputError):
        freeze_container(root=tmp_path,output_dir=tmp_path/"container",artifacts=artifacts,source_payload=source,
                         expected_source_sha256=canonical_sha(source),code_files=(code,),expected_code_sha256=(code_sha,),decision_wrappers=wrappers)


def test_partial_failure_never_writes_final_or_safe(tmp_path: Path, monkeypatch):
    source={"manifest":"fixed"}; code,code_sha=_portable_code(tmp_path); artifacts,wrappers=_artifacts()
    from tests.fixtures import phase6_s4_saved_delta_custody as custody
    original=custody.write_once; calls=[]
    def interrupted(**kwargs):
        calls.append(kwargs["destination"].name)
        if len(calls)==8: raise OSError("simulated interruption")
        return original(**kwargs)
    monkeypatch.setattr(custody,"write_once",interrupted)
    with pytest.raises(OSError):
        freeze_container(root=tmp_path,output_dir=tmp_path/"partial",artifacts=artifacts,source_payload=source,
                         expected_source_sha256=canonical_sha(source),code_files=(code,),expected_code_sha256=(code_sha,),decision_wrappers=wrappers)
    assert not (tmp_path/"partial"/"final-freeze.json").exists()
    assert not (tmp_path/"partial"/"safe-summary.json").exists()


def test_cross_link_change_is_rejected(tmp_path: Path):
    source={"manifest":"fixed"}; code,code_sha=_portable_code(tmp_path); artifacts,wrappers=_artifacts()
    artifacts["final-freeze.json"]["aggregate_sha256"]="b"*64
    artifacts["final-freeze.json"]["freeze_sha256"]=canonical_sha({k:v for k,v in artifacts["final-freeze.json"].items() if k!="freeze_sha256"})
    with pytest.raises(SavedDeltaInputError):
        freeze_container(root=tmp_path,output_dir=tmp_path/"cross",artifacts=artifacts,source_payload=source,expected_source_sha256=canonical_sha(source),code_files=(code,),expected_code_sha256=(code_sha,),decision_wrappers=wrappers)


@pytest.mark.parametrize("target",("extra","candidate_count","annotation_row","aggregate_row"))
def test_exact_artifact_shapes_reject_self_consistent_mutation(target):
    artifacts,wrappers=_artifacts()
    if target=="extra": artifacts["preflight.json"]["extra"]="x"
    elif target=="candidate_count": artifacts["candidate-manifest.json"]["counts"]["prior"]=166
    elif target=="annotation_row": artifacts["annotations.json"]["rows"][0]["extra"]="x"
    else: artifacts["aggregate.json"]["rows"][0]["decision"]["extra"]="x"
    with pytest.raises(SavedDeltaInputError): validate_artifact_set(artifacts,decision_wrappers=wrappers)
