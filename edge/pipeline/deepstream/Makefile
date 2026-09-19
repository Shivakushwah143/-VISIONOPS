# Custom nvinfer parser build. Requires a qualified DeepStream SDK install;
# this target was never executed in the development environment (no NVIDIA SDK).
DEEPSTREAM_ROOT ?= /opt/nvidia/deepstream/deepstream
CXX ?= g++
CXXFLAGS ?= -O2 -std=c++17 -fPIC -Wall -Wextra
PYTHON ?= python

all: libvisionops-parser.so

# visionops_contract.h and nvinfer.txt are generated from shared/model_contract.py.
# Regenerate before building so the parser can never embed a stale class mapping.
contract:
	$(PYTHON) -m scripts.gen_deepstream_contract

libvisionops-parser.so: parser.cpp visionops_contract.h
	$(CXX) $(CXXFLAGS) -I$(DEEPSTREAM_ROOT)/sources/includes -I. $< -o $@

clean:
	rm -f libvisionops-parser.so

.PHONY: all contract clean
