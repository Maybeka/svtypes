`include "svtypes_pkg.sv"
`include "examples/milestone_4_graph/generated_types.sv"

module tb;
  timeunit 1ns;
  timeprecision 1ps;

  initial begin
    byte unsigned bytes[$];
    int offset;
    GraphPair pair;
    GraphNode node;
    longint unsigned pair_id;

    pair = new();
    pair_id = pair.__svtypes_object_number;
    svtypes_pkg::register_object(pair);

    bytes.push_back(8'h02);
    for (int i = 0; i < 8; i++) begin
      bytes.push_back(pair_id[i * 8 +: 8]);
    end

    offset = 0;
    svtypes_pkg::object_packer#(GraphNode)::unpack(node, bytes, offset);
    $fatal(2, "Expected object reference type mismatch failure");
  end
endmodule
