from svtypes import *


t = Parameter[Int](1)

@svobj
class A(SvObject):
    a = Int()

print(__package__)
