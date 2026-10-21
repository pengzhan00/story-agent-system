-- 012_scene_keyframe_mode: how the keyframes of shots in this location are made. 'auto' = the project default
-- ([workflow] keyframe_from_references); 'compose' = from the approved portraits and the location image (image.edit,
-- consistent faces, slow); 'text' = from the prompt alone (fast).
ALTER TABLE scenes ADD COLUMN keyframe_mode TEXT NOT NULL DEFAULT 'auto' CHECK (keyframe_mode IN ('auto','compose','text'));
