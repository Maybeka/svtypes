`include "svtypes_pkg.sv"
`include "examples/milestone_4_graph/generated_types.sv"

module tb;
  timeunit 1ns;
  timeprecision 1ps;

  initial begin
    byte unsigned bytes[$];
    int offset;
    GraphNode node;

    bytes.push_back(8'h02);
    for (int i = 0; i < 8; i++) begin
      bytes.push_back(8'h00);
    end

    node = new();
    offset = 0;
    svtypes_pkg::object_packer#(GraphNode)::unpack(node, bytes, offset);
    $fatal(2, "Expected zero object reference failure");
  end
endmodule
