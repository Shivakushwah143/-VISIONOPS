// Canonical YOLO11 raw output parser. Build only against a qualified DeepStream SDK.
#include "nvdsinfer_custom_impl.h"
#include <algorithm>
#include <cmath>
extern "C" bool NvDsInferParseVisionOps(std::vector<NvDsInferLayerInfo> const& layers,
    NvDsInferNetworkInfo const& net, NvDsInferParseDetectionParams const& params,
    std::vector<NvDsInferObjectDetectionInfo>& objects) {
    if (layers.size()!=1 || params.numClassesConfigured!=3) return false;
    auto const& l=layers[0];
    if (l.dataType!=FLOAT || !l.buffer || l.inferDims.numDims!=2 || l.inferDims.d[0]!=7) return false;
    int n=l.inferDims.d[1]; auto p=static_cast<float const*>(l.buffer);
    for(int i=0;i<n;i++) {
        int cls=0; float confidence=p[4*n+i];
        for(int c=1;c<3;c++) if(p[(4+c)*n+i]>confidence){cls=c;confidence=p[(4+c)*n+i];}
        if (!std::isfinite(confidence) || confidence<params.perClassPreclusterThreshold[cls]) continue;
        float cx=p[i],cy=p[n+i],w=p[2*n+i],h=p[3*n+i];
        if(!std::isfinite(cx)||!std::isfinite(cy)||!std::isfinite(w)||!std::isfinite(h)||w<=0||h<=0) continue;
        float left=std::clamp(cx-w/2,0.f,float(net.width));
        float top=std::clamp(cy-h/2,0.f,float(net.height));
        float right=std::clamp(cx+w/2,0.f,float(net.width));
        float bottom=std::clamp(cy+h/2,0.f,float(net.height));
        if(right>left && bottom>top) objects.push_back({unsigned(cls),left,top,right-left,bottom-top,confidence});
    }
    return true;
}
CHECK_CUSTOM_PARSE_FUNC_PROTOTYPE(NvDsInferParseVisionOps);
