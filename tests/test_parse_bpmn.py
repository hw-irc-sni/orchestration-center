# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

# tests/test_parse_bpmn.py
import pytest
from unittest.mock import patch, MagicMock

from orchestrate.bpmn_flows.parse_bpmn import (
    BPMNFlowParser,
    BPMNParsingError,
    BPMNProcessNotFoundError,
)

VALID_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL">
  <process id="proc-1" name="Order Process">
    <laneSet>
      <lane id="lane-1" name="Sales">
        <flowNodeRef>start-1</flowNodeRef>
        <flowNodeRef>task-1</flowNodeRef>
      </lane>
    </laneSet>
    <startEvent id="start-1" name="Start">
      <outgoing>flow-1</outgoing>
    </startEvent>
    <task id="task-1" name="Review Order">
      <incoming>flow-1</incoming>
      <outgoing>flow-2</outgoing>
    </task>
    <exclusiveGateway id="gw-1" name="Approved?">
      <incoming>flow-2</incoming>
      <outgoing>flow-3</outgoing>
      <outgoing>flow-4</outgoing>
    </exclusiveGateway>
    <endEvent id="end-1" name="Approved">
      <incoming>flow-3</incoming>
    </endEvent>
    <endEvent id="end-2" name="Rejected">
      <incoming>flow-4</incoming>
    </endEvent>
    <sequenceFlow id="flow-1" sourceRef="start-1" targetRef="task-1" />
    <sequenceFlow id="flow-2" sourceRef="task-1" targetRef="gw-1" />
    <sequenceFlow id="flow-3" name="Yes" sourceRef="gw-1" targetRef="end-1" />
    <sequenceFlow id="flow-4" name="No" sourceRef="gw-1" targetRef="end-2" />
  </process>
</definitions>
"""

BILLION_LAUGHS_BPMN = """<?xml version="1.0"?>
<!DOCTYPE definitions [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<definitions><process id="proc-1">&lol2;</process></definitions>
"""


@pytest.fixture
def parser():
    with patch('orchestrate.bpmn_flows.parse_bpmn.get_llm_instance'):
        return BPMNFlowParser()


@pytest.fixture
def bpmn_file(tmp_path):
    path = tmp_path / "order.bpmn"
    path.write_text(VALID_BPMN)
    return str(path)


class TestLoadBpmn:
    """Test load_bpmn method"""

    def test_load_missing_file(self):
        with pytest.raises(BPMNParsingError, match="does not exist"):
            BPMNFlowParser.load_bpmn("/no/such/file.bpmn")

    def test_load_invalid_xml(self, tmp_path):
        path = tmp_path / "broken.bpmn"
        path.write_text("<definitions><process id='p1'></definitions>")

        with pytest.raises(BPMNParsingError, match="Invalid BPMN XML"):
            BPMNFlowParser.load_bpmn(str(path))

    def test_load_valid_file(self, bpmn_file):
        root = BPMNFlowParser.load_bpmn(bpmn_file)
        assert root is not None

    def test_load_rejects_entity_expansion(self, tmp_path):
        """Guards against billion-laughs style XML entity expansion attacks."""
        path = tmp_path / "bomb.bpmn"
        path.write_text(BILLION_LAUGHS_BPMN)

        with pytest.raises(BPMNParsingError, match="Disallowed BPMN XML content"):
            BPMNFlowParser.load_bpmn(str(path))


class TestFindProcesses:
    """Test find_processes / find_process"""

    def test_find_processes(self, bpmn_file):
        root = BPMNFlowParser.load_bpmn(bpmn_file)
        processes = BPMNFlowParser.find_processes(root)
        assert len(processes) == 1
        assert processes[0].attrib["id"] == "proc-1"

    def test_find_process_by_id(self, bpmn_file):
        root = BPMNFlowParser.load_bpmn(bpmn_file)
        process = BPMNFlowParser.find_process(root, "proc-1")
        assert process.attrib["id"] == "proc-1"

    def test_find_process_not_found(self, bpmn_file):
        root = BPMNFlowParser.load_bpmn(bpmn_file)
        with pytest.raises(BPMNProcessNotFoundError, match="missing-proc"):
            BPMNFlowParser.find_process(root, "missing-proc")


class TestExtractProcessFlow:
    """Test lane/node/flow extraction"""

    def test_extract_lanes(self, bpmn_file):
        root = BPMNFlowParser.load_bpmn(bpmn_file)
        process = BPMNFlowParser.find_process(root, "proc-1")
        lanes = BPMNFlowParser.extract_lanes(process)
        assert set(lanes.keys()) == {"lane-1"}
        assert lanes["lane-1"]["flow_node_refs"] == ["start-1", "task-1"]

    def test_extract_nodes(self, bpmn_file):
        root = BPMNFlowParser.load_bpmn(bpmn_file)
        process = BPMNFlowParser.find_process(root, "proc-1")
        nodes = BPMNFlowParser.extract_nodes(process)
        assert nodes["start-1"]["type"] == "startEvent"
        assert nodes["gw-1"]["type"] == "exclusiveGateway"
        assert nodes["gw-1"]["outgoing"] == ["flow-3", "flow-4"]

    def test_extract_sequence_flows(self, bpmn_file):
        root = BPMNFlowParser.load_bpmn(bpmn_file)
        process = BPMNFlowParser.find_process(root, "proc-1")
        flows = BPMNFlowParser.extract_sequence_flows(process)
        assert flows["flow-3"]["source_ref"] == "gw-1"
        assert flows["flow-3"]["target_ref"] == "end-1"

    def test_extract_process_flow_attaches_lanes(self, bpmn_file):
        root = BPMNFlowParser.load_bpmn(bpmn_file)
        process = BPMNFlowParser.find_process(root, "proc-1")
        flow = BPMNFlowParser.extract_process_flow(root, process)

        assert flow["nodes"]["task-1"]["lane_name"] == "Sales"
        assert len(flow["paths"]) == 2


class TestBuildOrderedPaths:
    """Test build_ordered_paths, including the exponential-blowup guard"""

    def test_branching_paths(self, bpmn_file):
        root = BPMNFlowParser.load_bpmn(bpmn_file)
        process = BPMNFlowParser.find_process(root, "proc-1")
        nodes = BPMNFlowParser.extract_nodes(process)
        flows = BPMNFlowParser.extract_sequence_flows(process)

        paths = BPMNFlowParser.build_ordered_paths(nodes, flows)
        assert len(paths) == 2
        assert paths[0][0] == "start-1"

    def test_cycle_detected(self):
        nodes = {
            "start": {"id": "start", "type": "startEvent"},
            "a": {"id": "a", "type": "task"},
        }
        flows = {
            "f1": {"source_ref": "start", "target_ref": "a"},
            "f2": {"source_ref": "a", "target_ref": "start"},
        }
        paths = BPMNFlowParser.build_ordered_paths(nodes, flows)
        assert any("[cycle detected]" in node_id for path in paths for node_id in path)

    def test_path_enumeration_is_capped(self):
        """A chain of N parallel gateways can produce up to 2^N paths; make sure
        enumeration stops at MAX_DISCOVERED_PATHS instead of blowing up."""
        nodes = {"start": {"id": "start", "type": "startEvent"}}
        flows = {}
        prev = "start"
        branch_count = 20  # uncapped this would yield 2**20 paths

        for i in range(branch_count):
            gw, a, b, join = f"gw{i}", f"a{i}", f"b{i}", f"join{i}"
            nodes[gw] = {"id": gw, "type": "parallelGateway"}
            nodes[a] = {"id": a, "type": "task"}
            nodes[b] = {"id": b, "type": "task"}
            nodes[join] = {"id": join, "type": "parallelGateway"}
            flows[f"{i}-in"] = {"source_ref": prev, "target_ref": gw}
            flows[f"{i}-a"] = {"source_ref": gw, "target_ref": a}
            flows[f"{i}-b"] = {"source_ref": gw, "target_ref": b}
            flows[f"{i}-a-join"] = {"source_ref": a, "target_ref": join}
            flows[f"{i}-b-join"] = {"source_ref": b, "target_ref": join}
            prev = join

        nodes["end"] = {"id": "end", "type": "endEvent"}
        flows["last"] = {"source_ref": prev, "target_ref": "end"}

        paths = BPMNFlowParser.build_ordered_paths(nodes, flows)
        assert len(paths) == BPMNFlowParser.MAX_DISCOVERED_PATHS


class TestConvertToMarkdown:
    """Test convert_to_markdown"""

    def test_convert_success(self, parser, bpmn_file):
        root = BPMNFlowParser.load_bpmn(bpmn_file)
        process = BPMNFlowParser.find_process(root, "proc-1")
        flow = BPMNFlowParser.extract_process_flow(root, process)

        mock_llm = MagicMock()
        mock_llm.ask_llm.return_value = ("prompt", "# Converted Markdown")
        parser.llm = mock_llm

        result = parser.convert_to_markdown(flow)
        assert result == "# Converted Markdown"
        mock_llm.ask_llm.assert_called_once()

    def test_convert_empty_flow(self, parser):
        with pytest.raises(BPMNParsingError, match="empty"):
            parser.convert_to_markdown({})

    def test_convert_llm_error(self, parser, bpmn_file):
        root = BPMNFlowParser.load_bpmn(bpmn_file)
        process = BPMNFlowParser.find_process(root, "proc-1")
        flow = BPMNFlowParser.extract_process_flow(root, process)

        mock_llm = MagicMock()
        mock_llm.ask_llm.side_effect = Exception("LLM error")
        parser.llm = mock_llm

        with pytest.raises(BPMNParsingError, match="LLM conversion failed"):
            parser.convert_to_markdown(flow)


class TestParseBpmnProcess:
    """Test the public parse_bpmn_process* entry points"""

    def test_parse_single_process(self, parser, bpmn_file):
        mock_llm = MagicMock()
        mock_llm.ask_llm.return_value = ("prompt", "# Markdown")
        parser.llm = mock_llm

        result = parser.parse_bpmn_process(bpmn_file, "proc-1")
        assert result == "# Markdown"

    def test_parse_process_not_found(self, parser, bpmn_file):
        with pytest.raises(BPMNProcessNotFoundError):
            parser.parse_bpmn_process(bpmn_file, "missing-proc")

    def test_parse_all_processes(self, parser, bpmn_file):
        mock_llm = MagicMock()
        mock_llm.ask_llm.return_value = ("prompt", "# Markdown")
        parser.llm = mock_llm

        result = parser.parse_bpmn_all_processes(bpmn_file)
        assert "# Markdown" in result

    def test_parse_all_processes_dict(self, parser, bpmn_file):
        mock_llm = MagicMock()
        mock_llm.ask_llm.return_value = ("prompt", "# Markdown")
        parser.llm = mock_llm

        result = parser.parse_bpmn_all_processes_dict(bpmn_file)
        assert result == {"proc-1": "# Markdown"}
