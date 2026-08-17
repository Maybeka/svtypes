`include "svtypes_pkg.sv"
`include "examples/milestone_3_svtypes_parity/generated_types.sv"

module tb_truncated;
  timeunit 1ns;
  timeprecision 1ps;

  initial begin
    byte unsigned bytes[$];
    int offset;
    ManyTypesTx tx;

    bytes.push_back(8'h01);
    tx = new();
    offset = 0;
    $display("M3 SvTypes truncated unpack test start");
    tx.unpack(bytes, offset);
    $fatal(2, "truncated unpack should not return");
  end
endmodule
