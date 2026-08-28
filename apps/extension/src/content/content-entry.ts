import{MockRecruitmentAdapter}from'../adapters/mock/mock-adapter';import{BossAdapter}from'../adapters/boss/boss-adapter';import{PageController}from'./page-controller';const adapter=[new MockRecruitmentAdapter(),new BossAdapter()].find(item=>item.canHandle(location.href));if(adapter)new PageController(adapter).start();

