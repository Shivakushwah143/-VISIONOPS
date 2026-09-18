DEEPSTREAM_ROOT ?= /opt/nvidia/deepstream/deepstream
CXX ?= g++
CXXFLAGS ?= -O2 -std=c++17 -fPIC -Wall -Wextra
libvisionops-parser.so: parser.cpp
	$(CXX) $(CXXFLAGS) -shared -I$(DEEPSTREAM_ROOT)/sources/includes $< -o $@
