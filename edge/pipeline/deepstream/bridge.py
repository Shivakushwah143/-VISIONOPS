"""NvDsBatchMeta adapter. Imported only inside a qualified DeepStream process."""
import pyds

def detections(buffer,coordinate_width=None,coordinate_height=None):
    batch=pyds.gst_buffer_get_nvds_batch_meta(hash(buffer));node=batch.frame_meta_list
    while node:
        frame=pyds.NvDsFrameMeta.cast(node.data);objects=[];item=frame.obj_meta_list
        while item:
            obj=pyds.NvDsObjectMeta.cast(item.data);r=obj.rect_params
            if obj.class_id in (0,1,2):objects.append({'class_id':obj.class_id,'track_id':str(obj.object_id),'confidence':float(obj.confidence),'bbox':[r.left/(coordinate_width or frame.source_frame_width),r.top/(coordinate_height or frame.source_frame_height),(r.left+r.width)/(coordinate_width or frame.source_frame_width),(r.top+r.height)/(coordinate_height or frame.source_frame_height)]})
            try:item=item.next
            except StopIteration:break
        yield {'source_id':frame.source_id,'frame_sequence':frame.frame_num,'pts':frame.buf_pts,'detections':objects}
        try:node=node.next
        except StopIteration:break
