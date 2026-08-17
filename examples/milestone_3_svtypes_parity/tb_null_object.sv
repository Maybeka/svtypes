`include "svtypes_pkg.sv"
`include "examples/milestone_3_svtypes_parity/generated_types.sv"

module tb_null_object;
  timeunit 1ns;
  timeprecision 1ps;

  initial begin
    byte unsigned bytes[$];
    int offset;
    M3Tx tx;
    M3Tx out;

    tx = new();
    tx.id = 1;
    tx.data[0] = 2;
    tx.data[1] = 3;
    tx.label = "null";
    tx.addr = 16'h1234;
    tx.serial = 64'h5;
    tx.color = RED;
    tx.ratio = 1.0;
    tx.temp = 2.0;
    tx.dyn = new[0];
    tx.q.delete();
    tx.inner = null;
    tx.pack(bytes);

    out = new();
    offset = 0;
    out.unpack(bytes, offset);
    if (offset != bytes.size()) $fatal(2, "offset=%0d size=%0d", offset, bytes.size());
    if (out.inner != null) $fatal(2, "expected null nested object");
    if (svtypes_pkg::get_object(out.__svtypes_object_number) != out) begin
      $fatal(2, "expected unpacked object to be registered by id");
    end

    $display("M3 SvTypes null-object pack/unpack test done");
    $finish;
  end
endmodule
