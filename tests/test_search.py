import unittest
from unittest.mock import patch
from app import director,scene_spec,render

class SearchTests(unittest.TestCase):
    def scene(self,values=None,target=17):
        return {'heading':'每次排除一半','narration':'比较中点，再缩小查找区间。','key':'查找17','detail':'有序数列','visual':'binary_search',
                'spec':{'parameters':{'target':target},'elements':[{'label':str(i+1),'value':v} for i,v in enumerate(values or [2,5,9,12,17,23,31,46])]}}
    def test_exact_steps_and_absent_target(self):
        steps=scene_spec.binary_search_steps([2,5,9,12,17,23,31,46],17)
        self.assertEqual([s['mid'] for s in steps],[3,5,4])
        self.assertEqual(steps[-1]['result'],4)
        steps=scene_spec.binary_search_steps([2,5,9],4)
        self.assertEqual(steps[-1]['result'],-1)
    def test_scene_checks_sorted_values_and_target(self):
        p=director.validate({'scenes':[self.scene()]})
        self.assertEqual(p['scenes'][0]['visual'],'binary_search')
        self.assertEqual(director.visual_issues(p),[])
        self.assertTrue(director.visual_issues(director.validate({'scenes':[self.scene([5,2,9])]})))
        self.assertTrue(director.visual_issues(director.validate({'scenes':[self.scene(target=None)]})))
    def test_invalid_values_are_not_silently_dropped_or_truncated(self):
        for values in ([1,2,3,4,5,6,7,8,9],[1,'invalid',3]):
            p=director.validate({'scenes':[self.scene(values)]})
            self.assertTrue(director.visual_issues(p))
            self.assertTrue(director.visual_issues(director.validate(p)))

    def test_light_frame_explains_result(self):
        s=director.validate({'scenes':[self.scene()]})['scenes'][0]
        with patch.object(render,'text_fit') as text:
            render.frame(s,0,1,4,4)
            labels=[str(c.args[2]) for c in text.call_args_list]
        self.assertTrue(any('第 5 项' in t for t in labels))

if __name__=='__main__':unittest.main()
