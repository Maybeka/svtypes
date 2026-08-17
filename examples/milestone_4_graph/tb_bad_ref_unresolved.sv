`include "svtypes_pkg.sv"
`include "examples/milestone_4_graph/generated_types.sv"

module tb;
  timeunit 1ns;
  timeprecision 1ps;

  initial begin
    byte unsigned bytes[$];
    int offset;
    GraphNode node;
    longint unsigned bad_id = 64'h0001_0000_0000_1234;

    bytes.push_back(8'h02);
    for (int i = 0; i < 8; i++) begin
      bytes.push_back(bad_id[i * 8 +: 8]);
    end

    node = new();
    offset = 0;
    svtypes_pkg::object_packer#(GraphNode)::unpack(node, bytes, offset);
    $fatal(2, "Expected unresolved object reference failure");
  end
endmodule
