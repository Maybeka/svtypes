class Packet;
  bit [12:0] opcode;
  logic [8:0] payload;
  function void fields(ref byte unsigned bytes[$], ref int offset, input bit decode);
    svtypes_pkg::field_codec#(type(this.opcode))::apply(this.opcode,bytes,offset,decode);
    svtypes_pkg::field_codec#(type(this.payload),svtypes_pkg::logic_packer#(type(this.payload)))::apply(this.payload,bytes,offset,decode);
  endfunction
endclass
class ExtendedPacket extends Packet;
  bit [20:0] fixed_data[5];
  bit [10:0] queue_data[$];
  function void fields(ref byte unsigned bytes[$], ref int offset, input bit decode);
    super.fields(bytes,offset,decode);
    svtypes_pkg::field_codec#(type(this.fixed_data),svtypes_pkg::fixed_array_packer#(type(this.fixed_data[0]),$size(this.fixed_data),svtypes_pkg::bit_packer#(type(this.fixed_data[0]))))::apply(this.fixed_data,bytes,offset,decode);
    svtypes_pkg::field_codec#(type(this.queue_data),svtypes_pkg::queue_packer#(type(this.queue_data[0]),svtypes_pkg::bit_packer#(type(this.queue_data[0]))))::apply(this.queue_data,bytes,offset,decode);
  endfunction
endclass
module tb;
 initial begin
   ExtendedPacket a,b;
   byte unsigned bytes[$];
   int offset;
   a=new(); b=new();
   a.opcode=13'h1234; a.payload=9'b10xz10101;
   foreach(a.fixed_data[i]) a.fixed_data[i]=21'h12345+i;
   a.queue_data={11'h123,11'h456};
   a.fields(bytes,offset,0);
   b.fields(bytes,offset,1);
   if (a.opcode !== b.opcode || a.payload !== b.payload || offset != bytes.size()) $fatal(1,"field visitor mismatch");
   if (a.fixed_data !== b.fixed_data || a.queue_data !== b.queue_data) $fatal(1,"container visitor mismatch");
   if (bytes.size() != 31) $fatal(1,"wire size mismatch");
   $display("SVTYPES_EDITABILITY_CONTAINERS_PASS bytes=%0d",bytes.size());
   $finish;
 end
endmodule
