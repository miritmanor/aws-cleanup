"""Connection types: Network interfaces, VPC placement, CloudFront origins, Route 53 records, WAF."""

from .vocabulary import (
    CONF_AUTHORITATIVE,
    ConnectionType,
    OWNERSHIP_OWNS,
    OWNERSHIP_SUPPORTING,
    SEM_INVOKES,
    SEM_OWNS,
    SEM_REFERENCES,
    SEM_SHARES_NETWORK,
)

TYPES = (
    # --- Network interfaces ------------------------------------------------
    ConnectionType(
        "eni.ec2.attachment", "NetworkInterface", ("EC2Instance",),
        "ENI Attachment.InstanceId", CONF_AUTHORITATIVE,
        "The instance an elastic network interface is attached to.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    ConnectionType(
        "eni.elasticip.association", "NetworkInterface", ("ElasticIP",),
        "ENI Association.AllocationId", CONF_AUTHORITATIVE,
        "The Elastic IP associated with an ENI - what actually makes the address billable.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "eni.securitygroup.membership", "NetworkInterface", ("SecurityGroup",),
        "ENI Groups", CONF_AUTHORITATIVE,
        "Security groups on an ENI. This is the authoritative view of SG usage, "
        "since a security group only takes effect through an ENI.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    # --- VPC network: placement, never ownership ---------------------------
    ConnectionType(
        "subnet.vpc.placement", "Subnet", ("VPC",),
        "Subnet VpcId", CONF_AUTHORITATIVE,
        "The VPC a subnet belongs to.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "routetable.vpc.placement", "RouteTable", ("VPC",),
        "RouteTable VpcId", CONF_AUTHORITATIVE,
        "The VPC a route table belongs to.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "routetable.subnet.association", "RouteTable", ("Subnet",),
        "RouteTable Associations.SubnetId", CONF_AUTHORITATIVE,
        "A subnet whose traffic this route table routes.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "routetable.internetgateway.route", "RouteTable", ("InternetGateway",),
        "RouteTable Routes.GatewayId", CONF_AUTHORITATIVE,
        "An internet gateway this route table sends traffic to; it makes the subnets it routes public.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "internetgateway.vpc.attachment", "InternetGateway", ("VPC",),
        "InternetGateway Attachments.VpcId", CONF_AUTHORITATIVE,
        "The VPC an internet gateway is attached to.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "ec2.subnet.placement", "EC2Instance", ("Subnet",),
        "instance SubnetId", CONF_AUTHORITATIVE,
        "The subnet an instance runs in.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "eni.subnet.placement", "NetworkInterface", ("Subnet",),
        "ENI SubnetId", CONF_AUTHORITATIVE,
        "The subnet a network interface is in.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "lambda.subnet.placement", "LambdaFunction", ("Subnet",),
        "function VpcConfig.SubnetIds", CONF_AUTHORITATIVE,
        "A subnet a VPC-attached function runs in.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "loadbalancer.subnet.placement", "LoadBalancer", ("Subnet",),
        "load balancer AvailabilityZones.SubnetId", CONF_AUTHORITATIVE,
        "A subnet a load balancer has a node in.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "rdsinstance.subnet.placement", "RDSInstance", ("Subnet",),
        "DBSubnetGroup.Subnets", CONF_AUTHORITATIVE,
        "A subnet in the database's subnet group, where it can run.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "networkfirewall.subnet.placement", "NetworkFirewall", ("Subnet",),
        "Firewall SubnetMappings", CONF_AUTHORITATIVE,
        "A subnet holding one of the firewall's endpoints - one per availability zone.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "transitgateway.vpc.attachment", "TransitGateway", ("VPC",),
        "TransitGatewayAttachments ResourceId", CONF_AUTHORITATIVE,
        "A VPC attached to a transit gateway, so it can route to the others attached.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "vpnconnection.transitgateway.attachment", "VPNConnection", ("TransitGateway",),
        "VpnConnection TransitGatewayId", CONF_AUTHORITATIVE,
        "The transit gateway a Site-to-Site VPN terminates on.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "natgateway.subnet.placement", "NatGateway", ("Subnet",),
        "NatGateway SubnetId", CONF_AUTHORITATIVE,
        "The subnet a NAT gateway sits in - a public one, for internet egress.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "natgateway.elasticip.allocation", "NatGateway", ("ElasticIP",),
        "NatGatewayAddresses.AllocationId", CONF_AUTHORITATIVE,
        "The Elastic IP a NAT gateway sends traffic from; it is why that address shows as associated.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "routetable.natgateway.route", "RouteTable", ("NatGateway",),
        "RouteTable Routes.NatGatewayId", CONF_AUTHORITATIVE,
        "A NAT gateway this route table sends internet traffic through; it makes the subnets it routes private with egress.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "vpcendpoint.vpc.placement", "VpcEndpoint", ("VPC",),
        "VpcEndpoint VpcId", CONF_AUTHORITATIVE,
        "The VPC an endpoint serves.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "vpcendpoint.subnet.placement", "VpcEndpoint", ("Subnet",),
        "VpcEndpoint SubnetIds", CONF_AUTHORITATIVE,
        "A subnet an interface endpoint has a network interface in; each is billed.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "vpcendpoint.securitygroup.membership", "VpcEndpoint", ("SecurityGroup",),
        "VpcEndpoint Groups", CONF_AUTHORITATIVE,
        "A security group on an interface endpoint.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "vpcendpoint.routetable.association", "VpcEndpoint", ("RouteTable",),
        "VpcEndpoint RouteTableIds", CONF_AUTHORITATIVE,
        "A route table a gateway endpoint adds its route to.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    # --- CloudFront origins -------------------------------------------------
    ConnectionType(
        "cloudfront.s3bucket.origin", "CloudFrontDistribution", ("S3Bucket",),
        "distribution Origins.DomainName", CONF_AUTHORITATIVE,
        "A bucket a distribution serves content from.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "cloudfront.loadbalancer.origin", "CloudFrontDistribution", ("LoadBalancer",),
        "distribution Origins.DomainName", CONF_AUTHORITATIVE,
        "A load balancer a distribution forwards requests to, named from its DNS name.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "cloudfront.apigateway.origin", "CloudFrontDistribution", ("APIGatewayRestApi", "APIGatewayV2Api"),
        "distribution Origins.DomainName", CONF_AUTHORITATIVE,
        "An API a distribution forwards requests to, from its execute-api domain.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    # --- Route 53 records --------------------------------------------------
    ConnectionType(
        "resolverendpoint.subnet.placement", "Route53ResolverEndpoint", ("Subnet",),
        "ListResolverEndpointIpAddresses SubnetId", CONF_AUTHORITATIVE,
        "A subnet holding one of a Resolver endpoint's IP addresses.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "resolverendpoint.securitygroup.membership", "Route53ResolverEndpoint", ("SecurityGroup",),
        "endpoint SecurityGroupIds", CONF_AUTHORITATIVE,
        "A security group on a Resolver endpoint's network interfaces.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "route53.cloudfront.alias", "Route53HostedZone", ("CloudFrontDistribution",),
        "record AliasTarget / CNAME", CONF_AUTHORITATIVE,
        "A record resolving to a CloudFront distribution, matched on the distribution's alias.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "route53.loadbalancer.alias", "Route53HostedZone", ("LoadBalancer",),
        "record AliasTarget / CNAME", CONF_AUTHORITATIVE,
        "A record resolving to a load balancer, named from its DNS name.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "route53.apigatewaydomain.alias", "Route53HostedZone", ("APIGatewayDomainName",),
        "record name, aliased to a d-... execute-api or CloudFront host", CONF_AUTHORITATIVE,
        "A record resolving to an API Gateway custom domain, which carries the record's name.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "route53.s3bucket.alias", "Route53HostedZone", ("S3Bucket",),
        "record AliasTarget / CNAME", CONF_AUTHORITATIVE,
        "A record resolving to an S3 website bucket, which must carry the record's name.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    # --- WAF ----------------------------------------------------------------
    ConnectionType(
        "wafwebacl.loadbalancer.protection", "WAFWebACL", ("LoadBalancer",),
        "ListResourcesForWebACL", CONF_AUTHORITATIVE,
        "A load balancer whose traffic this web ACL filters.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "wafwebacl.apigateway.protection", "WAFWebACL", ("APIGatewayRestApi",),
        "ListResourcesForWebACL", CONF_AUTHORITATIVE,
        "An API Gateway REST API stage whose traffic this web ACL filters.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "wafwebacl.cognito.protection", "WAFWebACL", ("CognitoUserPool",),
        "ListResourcesForWebACL", CONF_AUTHORITATIVE,
        "A Cognito user pool whose sign-in traffic this web ACL filters.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "cloudfront.wafwebacl.protected-by", "CloudFrontDistribution", ("WAFWebACL",),
        "distribution WebACLId", CONF_AUTHORITATIVE,
        "The web ACL filtering a distribution's traffic, recorded on the distribution.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
)
