// Canonical raw-tensor parser for the DeepStream / nvinfer path.
//
// Everything class-related is generated into visionops_contract.h from
// shared/model_contract.py, the same module the CPU ONNX adapter decodes with.
// The previous version hardcoded a 3-class, 7-channel tensor, which silently
// mis-parsed the qualified 10-class / 14-channel Hansung artifact. It now:
//
//   * requires exactly the published raw channel width (4 box + N sources),
//   * accepts [channels, anchors] or [anchors, channels] like the CPU decoder,
//   * scores only the mapped source columns and never remaps the rest,
//   * emits canonical class ids (0 person / 1 helmet / 2 no_helmet),
//   * uses the per-class configured threshold when DeepStream supplies one.
//
// Boxes stay in network coordinates; DeepStream applies the inverse of its
// configured maintain-aspect-ratio/symmetric-padding transform. Geometry parity
// against the CPU letterbox path is a hardware validation step, not an
// assumption, and is recorded as BLOCKED until an NVIDIA host runs it.
//
// Build: see Makefile (requires a qualified DeepStream SDK install).
#include "nvdsinfer_custom_impl.h"
#include "visionops_contract.h"

#include <algorithm>
#include <cmath>
#include <vector>

namespace {

inline bool Finite(float value) { return std::isfinite(value); }

bool ParseCanonicalTensor(const NvDsInferLayerInfo& layer, const NvDsInferNetworkInfo& net,
                          const NvDsInferParseDetectionParams& params,
                          std::vector<NvDsInferObjectDetectionInfo>& objects) {
    if (layer.dataType != FLOAT || layer.buffer == nullptr) return false;
    if (layer.inferDims.numDims != 2) return false;

    const int dim0 = static_cast<int>(layer.inferDims.d[0]);
    const int dim1 = static_cast<int>(layer.inferDims.d[1]);
    const bool channels_first = dim0 < dim1;
    const int channels = channels_first ? dim0 : dim1;
    const int anchors = channels_first ? dim1 : dim0;
    if (channels != VISIONOPS_RAW_CHANNELS || anchors <= 0) return false;

    const float* data = static_cast<const float*>(layer.buffer);
    // Read either layout without a transpose allocation; mirrors the numpy
    // transpose in shared/model_contract.py::decode.
    auto at = [&](int channel, int anchor) {
        return channels_first ? data[channel * anchors + anchor] : data[anchor * channels + channel];
    };

    for (int anchor = 0; anchor < anchors; ++anchor) {
        int matched = -1;
        float confidence = 0.0f;
        for (int mapped = 0; mapped < VISIONOPS_MAPPED_COUNT; ++mapped) {
            const float score = at(VISIONOPS_BOX_VALUES + VISIONOPS_MAPPED_SOURCE_INDEX[mapped], anchor);
            if (!Finite(score)) continue;
            if (matched < 0 || score > confidence) {
                matched = mapped;
                confidence = score;
            }
        }
        if (matched < 0) continue;

        const int canonical = VISIONOPS_MAPPED_CANONICAL_ID[matched];
        float threshold = VISIONOPS_SCORE_THRESHOLD;
        if (static_cast<size_t>(canonical) < params.perClassPreclusterThreshold.size()) {
            threshold = params.perClassPreclusterThreshold[canonical];
        }
        if (!Finite(confidence) || confidence < threshold) continue;

        const float cx = at(0, anchor);
        const float cy = at(1, anchor);
        const float width = at(2, anchor);
        const float height = at(3, anchor);
        if (!Finite(cx) || !Finite(cy) || !Finite(width) || !Finite(height)) continue;
        if (width <= 0.0f || height <= 0.0f) continue;

        const float left = std::clamp(cx - width / 2.0f, 0.0f, static_cast<float>(net.width));
        const float top = std::clamp(cy - height / 2.0f, 0.0f, static_cast<float>(net.height));
        const float right = std::clamp(cx + width / 2.0f, 0.0f, static_cast<float>(net.width));
        const float bottom = std::clamp(cy + height / 2.0f, 0.0f, static_cast<float>(net.height));
        if (right <= left || bottom <= top) continue;

        NvDsInferObjectDetectionInfo object;
        object.classId = static_cast<unsigned int>(canonical);
        object.left = left;
        object.top = top;
        object.width = right - left;
        object.height = bottom - top;
        object.detectionConfidence = confidence;
        objects.push_back(object);
    }
    return true;
}

}  // namespace

extern "C" bool NvDsInferParseVisionOps(std::vector<NvDsInferLayerInfo> const& layers,
                                        NvDsInferNetworkInfo const& net,
                                        NvDsInferParseDetectionParams const& params,
                                        std::vector<NvDsInferObjectDetectionInfo>& objects) {
    if (layers.size() != 1) return false;
    // The configured class count must cover every canonical id this parser emits.
    if (params.numClassesConfigured < VISIONOPS_CANONICAL_CLASS_COUNT) return false;
    return ParseCanonicalTensor(layers[0], net, params, objects);
}

CHECK_CUSTOM_PARSE_FUNC_PROTOTYPE(NvDsInferParseVisionOps);
