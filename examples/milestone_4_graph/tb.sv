`include "svtypes_pkg.sv"
`include "sv/svx_pkg.sv"
`include "examples/milestone_4_graph/generated_types.sv"

module tb;
  timeunit 1ns;
  timeprecision 1ps;

  import svx_pkg::*;

  initial begin
    chandle payload;
    byte unsigned bytes[$];
    int offset;
    GraphPair pair;
    GraphQueue queue_tx;
    GraphRefQueue ref_queue_tx;
    GraphNode node;
    GraphNode child;

    $display("M4 SvTypes graph parity test start @ %0.3f ns", $realtime);

    svx_init();
    svx_load("examples.milestone_4_graph.tests.parity_test");

    svx_start("examples.milestone_4_graph.tests.parity_test.python_puts_shared_pair");

    svx_channel_get_payload("m4.shared.py_to_sv", payload);
    if (svx_payload_kind(payload) != "svtypes") $fatal(2, "unexpected shared kind");
    if (svx_payload_type_name(payload) != "GraphPair") $fatal(2, "unexpected shared type");
    svx_payload_to_byte_queue(payload, bytes);
    svx_payload_destroy(payload);

    pair = new();
    offset = 0;
    pair.unpack(bytes, offset);
    if (offset != bytes.size()) $fatal(2, "shared unpack offset=%0d size=%0d", offset, bytes.size());
    if (pair.tag != 100) $fatal(2, "bad shared tag %0d", pair.tag);
    if (pair.left != pair.right) $fatal(2, "shared child identity was not preserved");
    if (pair.left.data != 101) $fatal(2, "bad shared child data %0d", pair.left.data);
    $display("SV unpacked shared graph size=%0d", bytes.size());

    svx_start("examples.milestone_4_graph.tests.parity_test.python_puts_self_cycle");

    svx_channel_get_payload("m4.self.py_to_sv", payload);
    if (svx_payload_kind(payload) != "svtypes") $fatal(2, "unexpected self kind");
    if (svx_payload_type_name(payload) != "GraphNode") $fatal(2, "unexpected self type");
    bytes.delete();
    svx_payload_to_byte_queue(payload, bytes);
    svx_payload_destroy(payload);

    node = new();
    offset = 0;
    node.unpack(bytes, offset);
    if (offset != bytes.size()) $fatal(2, "self unpack offset=%0d size=%0d", offset, bytes.size());
    if (node.data != 201) $fatal(2, "bad self data %0d", node.data);
    if (node.next != node) $fatal(2, "self-cycle identity was not preserved");
    $display("SV unpacked self graph size=%0d", bytes.size());

    svx_start("examples.milestone_4_graph.tests.parity_test.python_puts_queue_repeat");

    svx_channel_get_payload("m4.queue.py_to_sv", payload);
    if (svx_payload_kind(payload) != "svtypes") $fatal(2, "unexpected queue kind");
    if (svx_payload_type_name(payload) != "GraphQueue") $fatal(2, "unexpected queue type");
    bytes.delete();
    svx_payload_to_byte_queue(payload, bytes);
    svx_payload_destroy(payload);

    queue_tx = new();
    offset = 0;
    queue_tx.unpack(bytes, offset);
    if (offset != bytes.size()) $fatal(2, "queue unpack offset=%0d size=%0d", offset, bytes.size());
    if (queue_tx.tag != 500) $fatal(2, "bad queue tag %0d", queue_tx.tag);
    if (queue_tx.nodes.size() != 2) $fatal(2, "bad queue size %0d", queue_tx.nodes.size());
    if (queue_tx.nodes[0] != queue_tx.nodes[1]) begin
      $fatal(2, "queue repeated reference identity was not preserved");
    end
    if (queue_tx.nodes[0].data != 501) $fatal(2, "bad queue node data %0d", queue_tx.nodes[0].data);
    $display("SV unpacked queue graph size=%0d", bytes.size());

    svx_start("examples.milestone_4_graph.tests.parity_test.python_puts_ref_queue_repeat");

    svx_channel_get_payload("m4.ref_queue.py_to_sv", payload);
    if (svx_payload_kind(payload) != "svtypes") $fatal(2, "unexpected ref queue kind");
    if (svx_payload_type_name(payload) != "GraphRefQueue") $fatal(2, "unexpected ref queue type");
    bytes.delete();
    svx_payload_to_byte_queue(payload, bytes);
    svx_payload_destroy(payload);

    ref_queue_tx = new();
    offset = 0;
    ref_queue_tx.unpack(bytes, offset);
    if (offset != bytes.size()) $fatal(2, "ref queue unpack offset=%0d size=%0d", offset, bytes.size());
    if (ref_queue_tx.tag != 700) $fatal(2, "bad ref queue tag %0d", ref_queue_tx.tag);
    if (ref_queue_tx.nodes.size() != 2) $fatal(2, "bad ref queue size %0d", ref_queue_tx.nodes.size());
    if (ref_queue_tx.nodes[0] != ref_queue_tx.nodes[1]) begin
      $fatal(2, "ref queue repeated reference identity was not preserved");
    end
    if (ref_queue_tx.nodes[0].data != 701) $fatal(2, "bad ref queue node data %0d", ref_queue_tx.nodes[0].data);
    $display("SV unpacked ref queue graph size=%0d", bytes.size());

    fork
      begin
        svx_start("examples.milestone_4_graph.tests.parity_test.python_gets_shared_pair");
      end
      begin
        #(1.25ns);
        child = new();
        child.data = 301;
        child.next = null;
        pair = new();
        pair.tag = 300;
        pair.left = child;
        pair.right = child;
        bytes.delete();
        pair.pack(bytes);
        svx_channel_put_byte_queue(
          "m4.shared.sv_to_py",
          bytes,
          "svtypes",
          "GraphPair",
          "application/x-svtypes"
        );
      end
    join

    fork
      begin
        svx_start("examples.milestone_4_graph.tests.parity_test.python_gets_self_cycle");
      end
      begin
        #(1.25ns);
        node = new();
        node.data = 401;
        node.next = node;
        bytes.delete();
        node.pack(bytes);
        svx_channel_put_byte_queue(
          "m4.self.sv_to_py",
          bytes,
          "svtypes",
          "GraphNode",
          "application/x-svtypes"
        );
      end
    join

    fork
      begin
        svx_start("examples.milestone_4_graph.tests.parity_test.python_gets_queue_repeat");
      end
      begin
        #(1.25ns);
        child = new();
        child.data = 601;
        child.next = null;
        queue_tx = new();
        queue_tx.tag = 600;
        queue_tx.nodes.delete();
        queue_tx.nodes.push_back(child);
        queue_tx.nodes.push_back(child);
        bytes.delete();
        queue_tx.pack(bytes);
        svx_channel_put_byte_queue(
          "m4.queue.sv_to_py",
          bytes,
          "svtypes",
          "GraphQueue",
          "application/x-svtypes"
        );
      end
    join

    fork
      begin
        svx_start("examples.milestone_4_graph.tests.parity_test.python_gets_ref_queue_repeat");
      end
      begin
        #(1.25ns);
        child = new();
        child.data = 801;
        child.next = null;
        ref_queue_tx = new();
        ref_queue_tx.tag = 800;
        ref_queue_tx.nodes.delete();
        ref_queue_tx.nodes.push_back(child);
        ref_queue_tx.nodes.push_back(child);
        bytes.delete();
        ref_queue_tx.pack(bytes);
        svx_channel_put_byte_queue(
          "m4.ref_queue.sv_to_py",
          bytes,
          "svtypes",
          "GraphRefQueue",
          "application/x-svtypes"
        );
      end
    join

    $display("M4 SvTypes graph parity test done @ %0.3f ns", $realtime);
    $finish;
  end
endmodule
