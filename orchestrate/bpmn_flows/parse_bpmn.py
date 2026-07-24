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

from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
import xml.etree.ElementTree as ET
import concurrent.futures

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import parse as parse_untrusted_xml
from loguru import logger

from common.llm import get_llm_instance


class BPMNParsingError(Exception):
    """Base exception for BPMN parsing errors."""
    pass


class BPMNProcessNotFoundError(BPMNParsingError):
    """Raised when a specific BPMN process is not found."""
    pass


class BPMNFlowParser:
    """Parser for extracting BPMN process flows and converting them to markdown."""

    FLOW_NODE_TAGS = {
        "startEvent",
        "endEvent",
        "intermediateCatchEvent",
        "intermediateThrowEvent",
        "boundaryEvent",
        "task",
        "userTask",
        "serviceTask",
        "scriptTask",
        "manualTask",
        "businessRuleTask",
        "sendTask",
        "receiveTask",
        "subProcess",
        "callActivity",
        "exclusiveGateway",
        "parallelGateway",
        "inclusiveGateway",
        "eventBasedGateway",
        "complexGateway",
    }

    # Caps the number of paths build_ordered_paths() will enumerate. Without a
    # cap, a diagram with N gateways chained in series can produce up to 2^N
    # paths, letting a small, well-formed upload exhaust CPU/memory.
    MAX_DISCOVERED_PATHS = 500

    def __init__(self):
        self.llm = get_llm_instance()

    @staticmethod
    def _strip_namespace(tag: str) -> str:
        if "}" in tag:
            return tag.split("}", 1)[1]
        return tag

    @staticmethod
    def _get_attr(element: ET.Element, key: str, default: str = "") -> str:
        return element.attrib.get(key, default)

    @classmethod
    def _is_bpmn_tag(cls, element: ET.Element, tag_name: str) -> bool:
        return cls._strip_namespace(element.tag) == tag_name

    @staticmethod
    def load_bpmn(bpmn_path: str) -> ET.Element:
        path = Path(bpmn_path)
        if not path.exists():
            raise BPMNParsingError(f"BPMN file does not exist: {bpmn_path}")

        try:
            # Untrusted, user-uploaded XML: parse with defusedxml to reject DTDs
            # that declare entities (billion-laughs) or reference external/network
            # resources (XXE) instead of the stdlib xml.etree.ElementTree parser.
            tree = parse_untrusted_xml(str(path))
            return tree.getroot()
        except DefusedXmlException as e:
            raise BPMNParsingError(f"Disallowed BPMN XML content: {e}") from e
        except ET.ParseError as e:
            raise BPMNParsingError(f"Invalid BPMN XML: {e}") from e
        except Exception as e:
            raise BPMNParsingError(f"Cannot open BPMN file: {e}") from e

    @classmethod
    def find_processes(cls, root: ET.Element) -> List[ET.Element]:
        return [
            element for element in root.iter()
            if cls._is_bpmn_tag(element, "process")
        ]

    @classmethod
    def find_process(cls, root: ET.Element, process_id: str) -> ET.Element:
        for process in cls.find_processes(root):
            if cls._get_attr(process, "id") == process_id:
                return process

        raise BPMNProcessNotFoundError(f"Process not found: {process_id}")

    @classmethod
    def extract_lanes(cls, process: ET.Element) -> Dict[str, Dict[str, Any]]:
        lanes = {}

        for lane in process.iter():
            if not cls._is_bpmn_tag(lane, "lane"):
                continue

            lane_id = cls._get_attr(lane, "id")
            if not lane_id:
                continue

            flow_refs = [
                child.text.strip()
                for child in lane
                if cls._is_bpmn_tag(child, "flowNodeRef") and child.text
            ]

            lanes[lane_id] = {
                "id": lane_id,
                "name": cls._get_attr(lane, "name", lane_id),
                "flow_node_refs": flow_refs,
            }

        return lanes

    @classmethod
    def extract_nodes(cls, process: ET.Element) -> Dict[str, Dict[str, Any]]:
        nodes = {}

        for element in process.iter():
            tag = cls._strip_namespace(element.tag)

            if tag not in cls.FLOW_NODE_TAGS:
                continue

            node_id = cls._get_attr(element, "id")
            if not node_id:
                continue

            incoming = [
                child.text.strip()
                for child in element
                if cls._is_bpmn_tag(child, "incoming") and child.text
            ]

            outgoing = [
                child.text.strip()
                for child in element
                if cls._is_bpmn_tag(child, "outgoing") and child.text
            ]

            nodes[node_id] = {
                "id": node_id,
                "name": cls._get_attr(element, "name", node_id),
                "type": tag,
                "incoming": incoming,
                "outgoing": outgoing,
            }

        return nodes

    @classmethod
    def extract_sequence_flows(cls, process: ET.Element) -> Dict[str, Dict[str, Any]]:
        flows = {}

        for element in process.iter():
            if not cls._is_bpmn_tag(element, "sequenceFlow"):
                continue

            flow_id = cls._get_attr(element, "id")
            if not flow_id:
                continue

            flows[flow_id] = {
                "id": flow_id,
                "name": cls._get_attr(element, "name", flow_id),
                "source_ref": cls._get_attr(element, "sourceRef"),
                "target_ref": cls._get_attr(element, "targetRef"),
            }

        return flows

    @classmethod
    def extract_message_flows(cls, root: ET.Element) -> Dict[str, Dict[str, Any]]:
        flows = {}

        for element in root.iter():
            if not cls._is_bpmn_tag(element, "messageFlow"):
                continue

            flow_id = cls._get_attr(element, "id")
            if not flow_id:
                continue

            flows[flow_id] = {
                "id": flow_id,
                "name": cls._get_attr(element, "name", flow_id),
                "source_ref": cls._get_attr(element, "sourceRef"),
                "target_ref": cls._get_attr(element, "targetRef"),
            }

        return flows

    @staticmethod
    def attach_lane_info(
        nodes: Dict[str, Dict[str, Any]],
        lanes: Dict[str, Dict[str, Any]],
    ) -> None:
        for lane_id, lane in lanes.items():
            for node_id in lane["flow_node_refs"]:
                if node_id in nodes:
                    nodes[node_id]["lane_id"] = lane_id
                    nodes[node_id]["lane_name"] = lane["name"]

    @classmethod
    def build_ordered_paths(
        cls,
        nodes: Dict[str, Dict[str, Any]],
        flows: Dict[str, Dict[str, Any]],
    ) -> List[List[str]]:
        outgoing_by_node: Dict[str, List[Dict[str, Any]]] = {}

        for flow in flows.values():
            source_ref = flow.get("source_ref")
            if source_ref:
                outgoing_by_node.setdefault(source_ref, []).append(flow)

        start_nodes = [
            node_id for node_id, node in nodes.items()
            if node["type"] == "startEvent"
        ]

        paths: List[List[str]] = []
        truncated = False

        def walk(node_id: str, current_path: List[str], visited: set) -> None:
            nonlocal truncated
            if len(paths) >= cls.MAX_DISCOVERED_PATHS:
                truncated = True
                return

            if node_id in visited:
                paths.append(current_path + [f"{node_id} [cycle detected]"])
                return

            node = nodes.get(node_id)
            if not node:
                paths.append(current_path + [f"{node_id} [missing node]"])
                return

            next_path = current_path + [node_id]

            if node["type"] == "endEvent" or node_id not in outgoing_by_node:
                paths.append(next_path)
                return

            for flow in outgoing_by_node[node_id]:
                if len(paths) >= cls.MAX_DISCOVERED_PATHS:
                    truncated = True
                    break
                target_ref = flow.get("target_ref")
                if target_ref:
                    walk(target_ref, next_path, visited | {node_id})

        for start_node in start_nodes:
            if len(paths) >= cls.MAX_DISCOVERED_PATHS:
                truncated = True
                break
            walk(start_node, [], set())

        if not paths and nodes:
            logger.warning("No start event found; ordered paths could not be built")

        if truncated:
            logger.warning(
                f"Path enumeration truncated at {cls.MAX_DISCOVERED_PATHS} paths; "
                "diagram has more branches than can be safely enumerated"
            )

        return paths

    @classmethod
    def extract_process_flow(cls, root: ET.Element, process: ET.Element) -> Dict[str, Any]:
        lanes = cls.extract_lanes(process)
        nodes = cls.extract_nodes(process)
        sequence_flows = cls.extract_sequence_flows(process)
        message_flows = cls.extract_message_flows(root)

        cls.attach_lane_info(nodes, lanes)

        return {
            "process_id": cls._get_attr(process, "id"),
            "process_name": cls._get_attr(process, "name", cls._get_attr(process, "id")),
            "lanes": lanes,
            "nodes": nodes,
            "sequence_flows": sequence_flows,
            "message_flows": message_flows,
            "paths": cls.build_ordered_paths(nodes, sequence_flows),
        }

    def parse_bpmn_process_flow(self, bpmn_path: str, process_id: str) -> Dict[str, Any]:
        root = self.load_bpmn(bpmn_path)
        process = self.find_process(root, process_id)
        return self.extract_process_flow(root, process)

    def parse_bpmn_all_process_flows(self, bpmn_path: str) -> Dict[str, Dict[str, Any]]:
        root = self.load_bpmn(bpmn_path)
        processes = self.find_processes(root)

        result = {}

        for process in processes:
            process_id = self._get_attr(process, "id")
            if not process_id:
                logger.warning("Skipping BPMN process without ID")
                continue

            result[process_id] = self.extract_process_flow(root, process)

        logger.info(f"Extracted {len(result)} BPMN processes")
        return result

    @staticmethod
    def flow_to_text(flow: Dict[str, Any]) -> str:
        lines = []

        lines.append(f"Process: {flow.get('process_name')}")
        lines.append(f"Process ID: {flow.get('process_id')}")
        lines.append("")

        lines.append("Nodes:")
        for node in flow["nodes"].values():
            lane = node.get("lane_name", "")
            lane_text = f" | Lane: {lane}" if lane else ""
            lines.append(
                f"- {node['id']} | {node['type']} | {node['name']}{lane_text}"
            )

        lines.append("")
        lines.append("Sequence Flows:")
        for seq in flow["sequence_flows"].values():
            lines.append(
                f"- {seq['id']}: {seq['source_ref']} -> {seq['target_ref']} | {seq['name']}"
            )

        if flow["message_flows"]:
            lines.append("")
            lines.append("Message Flows:")
            for msg in flow["message_flows"].values():
                lines.append(
                    f"- {msg['id']}: {msg['source_ref']} -> {msg['target_ref']} | {msg['name']}"
                )

        if flow["paths"]:
            lines.append("")
            lines.append("Discovered Paths:")
            for index, path in enumerate(flow["paths"], start=1):
                readable_path = []
                for node_id in path:
                    clean_node_id = node_id.replace(" [cycle detected]", "").replace(" [missing node]", "")
                    node_name = flow["nodes"].get(clean_node_id, {}).get("name", node_id)
                    readable_path.append(node_name)

                lines.append(f"{index}. " + " -> ".join(readable_path))

        return "\n".join(lines)

    @staticmethod
    def build_markdown_prompt(flow_text: str) -> str:
        return f"""
Convert the following BPMN process flow extraction into well-formed Markdown.

Requirements:
1. Preserve all BPMN process information.
2. Use headings for process name, lanes, nodes, sequence flows, message flows, and paths.
3. Format nodes, sequence flows, and message flows as Markdown tables.
4. Explain gateways and branches clearly.
5. Do not omit IDs, names, types, source references, or target references.
6. Output only Markdown.
7. Output in English.

BPMN flow content:
{flow_text}
"""

    def convert_to_markdown(self, flow: Dict[str, Any]) -> str:
        if not flow:
            raise BPMNParsingError("BPMN flow data is empty")

        flow_text = self.flow_to_text(flow)
        if not flow_text.strip():
            raise BPMNParsingError("BPMN flow text is empty")

        prompt = self.build_markdown_prompt(flow_text)

        try:
            _, res = self.llm.ask_llm(prompt)
        except Exception as e:
            raise BPMNParsingError(f"LLM conversion failed: {e}") from e

        if not res or not res.strip():
            raise BPMNParsingError("LLM conversion returned empty markdown")

        return res

    def convert_process_to_markdown(
        self,
        process_item: Tuple[str, Dict[str, Any]],
    ) -> Tuple[str, str]:
        process_id, flow = process_item
        markdown_content = self.convert_to_markdown(flow)
        logger.info(f"Successfully converted BPMN process: {process_id}")
        return process_id, markdown_content

    def convert_all_processes_to_markdown(
        self,
        processes_dict: Dict[str, Dict[str, Any]],
        max_workers: int = 4,
    ) -> Dict[str, str]:
        markdown_dict = {}

        process_ids = list(processes_dict.keys())
        results: List[Optional[Tuple[str, str]]] = [None] * len(process_ids)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(
                    self.convert_process_to_markdown,
                    (process_id, processes_dict[process_id]),
                ): i
                for i, process_id in enumerate(process_ids)
            }

            for future in concurrent.futures.as_completed(future_to_index):
                index = future_to_index[future]
                process_id = process_ids[index]

                try:
                    results[index] = future.result()
                except Exception as e:
                    logger.error(f"Failed to convert BPMN process '{process_id}': {e}")
                    raise BPMNParsingError(
                        f"Failed to convert BPMN process '{process_id}' to markdown: {e}"
                    ) from e

        for result in results:
            if result:
                process_id, content = result
                markdown_dict[process_id] = content

        return markdown_dict

    def parse_bpmn_process(self, bpmn_path: str, process_id: str) -> str:
        """
        Parse a specific BPMN process and return markdown.

        Markdown conversion is mandatory.
        """
        flow = self.parse_bpmn_process_flow(bpmn_path, process_id)
        return self.convert_to_markdown(flow)

    def parse_bpmn_all_processes(
        self,
        bpmn_path: str,
        max_workers: int = 4,
    ) -> str:
        """
        Parse all BPMN processes and return one combined markdown string.

        Markdown conversion is mandatory.
        """
        try:
            processes_dict = self.parse_bpmn_all_process_flows(bpmn_path)
            logger.info(f"Extracted {len(processes_dict)} BPMN processes")

            markdown_dict = self.convert_all_processes_to_markdown(
                processes_dict,
                max_workers=max_workers,
            )

            logger.info(
                f"Successfully converted {len(markdown_dict)} BPMN processes to markdown"
            )

            return "\n\n---\n\n".join(markdown_dict.values())

        except Exception as e:
            logger.error(f"Failed to parse BPMN flows: {e}")
            raise BPMNParsingError(f"Failed to parse BPMN flows: {e}") from e

    def parse_bpmn_all_processes_dict(
        self,
        bpmn_path: str,
        max_workers: int = 4,
    ) -> Dict[str, str]:
        """
        Parse all BPMN processes and return a dict of process_id -> markdown.

        Useful for callers (e.g. BPMNFlowManager) that want to store or index
        each process individually rather than a single concatenated string.
        """
        processes_dict = self.parse_bpmn_all_process_flows(bpmn_path)
        logger.info(f"Extracted {len(processes_dict)} BPMN processes")

        markdown_dict = self.convert_all_processes_to_markdown(
            processes_dict,
            max_workers=max_workers,
        )

        logger.info(
            f"Successfully converted {len(markdown_dict)} BPMN processes to markdown"
        )

        return markdown_dict
