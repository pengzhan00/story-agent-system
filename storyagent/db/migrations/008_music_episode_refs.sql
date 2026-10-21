-- 008_music_episode_refs: a scene BGM is named after the episode it is for ("ep02_act01_scene02"), so episode 2's
-- cues no longer collide with episode 1's. Existing cues belonged to episode 1.
UPDATE music_themes SET scene_ref = 'ep01_' || scene_ref
 WHERE kind = 'bgm' AND scene_ref <> '' AND scene_ref NOT LIKE 'ep%';
