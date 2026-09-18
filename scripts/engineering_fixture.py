"""Synthetic engineering fixture only. No trained PPE or quality claims."""
from pathlib import Path
import json,numpy as np,cv2,onnx
from onnx import helper,TensorProto,numpy_helper

def create(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    # Standard YOLO raw tensor: xywh + three classes. Only two fixture boxes score.
    output=np.zeros((1,7,20),np.float32);output[0,:,0]=[320,320,300,600,.99,0,0];output[0,:,1]=[320,100,80,90,0,0,.98]
    node=helper.make_node('Constant',[],['output'],value=numpy_helper.from_array(output))
    graph=helper.make_graph([node],'EXPLICIT_SIMULATION_FIXTURE',[helper.make_tensor_value_info('images',TensorProto.FLOAT,[1,3,640,640])],[helper.make_tensor_value_info('output',TensorProto.FLOAT,[1,7,20])]);model=helper.make_model(graph,opset_imports=[helper.make_opsetid('',17)]);model.ir_version=9
    helper.set_model_props(model,{'names':"{0: 'person', 1: 'helmet', 2: 'no_helmet'}",'run_kind':'simulation_fixture'});onnx.save(model,folder/'simulation.onnx')
    frame=np.full((640,640,3),200,np.uint8);cv2.putText(frame,'SYNTHETIC ENGINEERING FIXTURE',(35,330),cv2.FONT_HERSHEY_SIMPLEX,.65,(0,0,0),2);cv2.imwrite(str(folder/'warmup.jpg'),frame)
    video=cv2.VideoWriter(str(folder/'engineering.avi'),cv2.VideoWriter_fourcc(*'MJPG'),10,(640,640))
    if not video.isOpened():raise RuntimeError('video_encoder_unavailable')
    for _ in range(60):video.write(frame)
    video.release();return folder
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',default='var/engineering');a=p.parse_args();create(a.output);print('Created explicitly synthetic fixtures. These do not verify real PPE detection.')
